#!/usr/bin/env python3
"""
Convert ISO20022-like XSD files (request + response) directly to OpenAPI 3.0.1 YAML.

Usage:
  python xsd_to_openapi.py <message_name> <output_path> <request_xsd> <response_xsd>

Example:
  python xsd_to_openapi.py AuthorisationInitiation AuthorisationInitiation.yaml \\
      cain.001.001.03.xsd cain.002.001.03.xsd
"""

import argparse
import xml.etree.ElementTree as ET
import os
import sys

try:
    import yaml
except ImportError:
    print("PyYAML not found. Install with: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

XS = '{http://www.w3.org/2001/XMLSchema}'

# XSD primitive types that map to OpenAPI "string"
STRING_BASE_TYPES = {
    'string', 'normalizedString', 'token', 'language',
    'Name', 'NCName', 'ID', 'IDREF', 'NMTOKEN',
    'anyURI', 'base64Binary', 'hexBinary',
    'date', 'dateTime', 'time',
    'gYear', 'gYearMonth', 'gMonthDay', 'gDay', 'gMonth',
}

# XSD primitive types that map to OpenAPI "integer"
INTEGER_BASE_TYPES = {
    'integer', 'long', 'int', 'short', 'byte',
    'nonNegativeInteger', 'positiveInteger',
    'unsignedLong', 'unsignedInt', 'unsignedShort', 'unsignedByte',
}

# XSD primitive types that map to OpenAPI "number"
NUMBER_BASE_TYPES = {'decimal', 'float', 'double'}

# All XSD primitives combined
ALL_PRIMITIVE_TYPES = STRING_BASE_TYPES | INTEGER_BASE_TYPES | NUMBER_BASE_TYPES | {'boolean', 'anyType'}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def strip_ns(type_str: str) -> str:
    """Strip XML namespace prefix (e.g. 'xs:' or 'n1:') from a type name."""
    return type_str.split(':', 1)[-1] if ':' in type_str else type_str


def is_xs_primitive(type_name: str) -> bool:
    """Return True if type_name is an XSD primitive (with or without xs: prefix)."""
    return strip_ns(type_name) in ALL_PRIMITIVE_TYPES


def primitive_schema(local_name: str) -> dict:
    """Return inline OpenAPI schema dict for an XSD primitive type local name."""
    if local_name in STRING_BASE_TYPES:
        return {'type': 'string'}
    if local_name in INTEGER_BASE_TYPES:
        return {'type': 'integer'}
    if local_name in NUMBER_BASE_TYPES:
        return {'type': 'number'}
    if local_name == 'boolean':
        return {'type': 'boolean'}
    if local_name == 'anyType':
        return {'type': 'object'}
    return {'type': 'string'}


def type_ref_to_schema(type_name: str) -> dict:
    """
    Convert element type= attribute to an OpenAPI schema dict.
    XSD primitives (xs:string, xs:boolean, …) → inline type.
    Named types → $ref.
    """
    local = strip_ns(type_name)
    if is_xs_primitive(type_name):
        return primitive_schema(local)
    return {'$ref': f'#/components/schemas/{local}'}


# ---------------------------------------------------------------------------
# XSD → OpenAPI schema converters
# ---------------------------------------------------------------------------

def convert_simple_type(elem) -> dict:
    """Convert an xs:simpleType element to an OpenAPI schema dict."""
    restriction = elem.find(f'{XS}restriction')
    if restriction is None:
        # xs:union / xs:list – treat as plain string
        return {'type': 'string'}

    base = restriction.get('base', 'xs:string')
    base_local = strip_ns(base)

    # ---- Enumeration -------------------------------------------------------
    enums = restriction.findall(f'{XS}enumeration')
    if enums:
        return {
            'type': 'string',
            'enum': [e.get('value') for e in enums],
        }

    schema: dict = {}

    # ---- String / binary / date variants -----------------------------------
    if base_local in STRING_BASE_TYPES or base_local == 'string':
        length_el   = restriction.find(f'{XS}length')
        min_len_el  = restriction.find(f'{XS}minLength')
        max_len_el  = restriction.find(f'{XS}maxLength')
        pattern_el  = restriction.find(f'{XS}pattern')

        if length_el is not None:
            val = int(length_el.get('value'))
            schema['minLength'] = val
            schema['maxLength'] = val
        else:
            if min_len_el is not None:
                schema['minLength'] = int(min_len_el.get('value'))
            if max_len_el is not None:
                schema['maxLength'] = int(max_len_el.get('value'))

        if pattern_el is not None:
            schema['pattern'] = f'^({pattern_el.get("value")})$'

        schema['type'] = 'string'

    # ---- Number (decimal / float / double) ---------------------------------
    elif base_local in NUMBER_BASE_TYPES:
        min_el = restriction.find(f'{XS}minInclusive')
        max_el = restriction.find(f'{XS}maxInclusive')
        if min_el is not None:
            v = min_el.get('value')
            schema['minimum'] = float(v) if '.' in v else int(v)
        if max_el is not None:
            v = max_el.get('value')
            schema['maximum'] = float(v) if '.' in v else int(v)
        schema['type'] = 'number'

    # ---- Integer variants --------------------------------------------------
    elif base_local in INTEGER_BASE_TYPES:
        min_el = restriction.find(f'{XS}minInclusive')
        max_el = restriction.find(f'{XS}maxInclusive')
        if min_el is not None:
            schema['minimum'] = int(min_el.get('value'))
        if max_el is not None:
            schema['maximum'] = int(max_el.get('value'))
        schema['type'] = 'integer'

    # ---- Boolean -----------------------------------------------------------
    elif base_local == 'boolean':
        schema['type'] = 'boolean'

    # ---- Fallback ----------------------------------------------------------
    else:
        schema['type'] = 'string'

    return schema


def parse_content_elements(content_elem, is_choice: bool = False):
    """
    Walk the direct children of xs:sequence / xs:choice / xs:all and
    return (properties_dict, required_list).
    """
    properties: dict = {}
    required: list = []

    for child in content_elem:
        local = child.tag.replace(XS, '')

        if local == 'element':
            name      = child.get('name')
            type_ref  = child.get('type')
            max_occ   = child.get('maxOccurs', '1')
            min_occ   = child.get('minOccurs', '1')
            ref_attr  = child.get('ref')

            # xs:element ref="..." (group-style reference)
            if ref_attr and not name:
                name = strip_ns(ref_attr)

            if not name:
                continue

            # Resolve property schema
            if type_ref:
                prop = type_ref_to_schema(type_ref)
            elif ref_attr:
                ref_local = strip_ns(ref_attr)
                prop = {'$ref': f'#/components/schemas/{ref_local}'}
            else:
                inline_simple   = child.find(f'{XS}simpleType')
                inline_complex  = child.find(f'{XS}complexType')
                if inline_simple is not None:
                    prop = convert_simple_type(inline_simple)
                elif inline_complex is not None:
                    prop = convert_complex_type(inline_complex)
                else:
                    prop = {'type': 'string'}

            # Array? (maxOccurs="unbounded" or numeric > 1)
            is_array = max_occ == 'unbounded' or (
                max_occ.isdigit() and int(max_occ) > 1
            )
            if is_array:
                min_occ_int = int(min_occ) if min_occ.isdigit() else 0
                array_schema: dict = {}
                # Within a choice all branches are optional alternatives,
                # so minItems from minOccurs is not meaningful.
                if min_occ_int >= 1 and not is_choice:
                    array_schema['minItems'] = min_occ_int
                array_schema['type'] = 'array'
                array_schema['items'] = prop
                prop = array_schema

            properties[name] = prop

            # Required in a sequence/all unless minOccurs="0"
            if not is_choice and min_occ != '0':
                required.append(name)

        elif local in ('sequence', 'all'):
            # Nested sequence/all – recurse, still not a choice
            sub_props, sub_req = parse_content_elements(child, is_choice=False)
            properties.update(sub_props)
            if not is_choice:
                required.extend(sub_req)

        elif local == 'choice':
            # Nested choice – elements are optional
            sub_props, _ = parse_content_elements(child, is_choice=True)
            properties.update(sub_props)

        # Silently skip: annotation, any, anyAttribute, group,
        # attributeGroup, attribute

    return properties, required


def convert_complex_type(elem) -> dict:
    """Convert an xs:complexType element to an OpenAPI schema dict."""

    # ---- complexContent (extension / restriction) --------------------------
    cc = elem.find(f'{XS}complexContent')
    if cc is not None:
        for kind in ('extension', 'restriction'):
            inner = cc.find(f'{XS}{kind}')
            if inner is not None:
                base        = inner.get('base', '')
                base_local  = strip_ns(base)
                inner_seq   = inner.find(f'{XS}sequence')
                inner_cho   = inner.find(f'{XS}choice')
                inner_all   = inner.find(f'{XS}all')
                content     = inner_seq or inner_cho or inner_all

                if content is not None:
                    props, req = parse_content_elements(
                        content, is_choice=(inner_cho is not None)
                    )
                    add_schema: dict = {
                        'type': 'object',
                        'properties': dict(sorted(props.items())),
                    }
                    if req:
                        add_schema = {'required': sorted(req), **add_schema}
                    if base_local:
                        return {'allOf': [
                            {'$ref': f'#/components/schemas/{base_local}'},
                            add_schema,
                        ]}
                    return add_schema

                elif base_local:
                    return {'allOf': [{'$ref': f'#/components/schemas/{base_local}'}]}
        return {'type': 'object'}

    # ---- simpleContent (typed value + attributes) --------------------------
    sc = elem.find(f'{XS}simpleContent')
    if sc is not None:
        for kind in ('extension', 'restriction'):
            inner = sc.find(f'{XS}{kind}')
            if inner is not None:
                base = inner.get('base', 'xs:string')
                base_local = strip_ns(base)
                if is_xs_primitive(base):
                    return primitive_schema(base_local)
                return {'allOf': [{'$ref': f'#/components/schemas/{base_local}'}]}
        return {'type': 'string'}

    # ---- Normal sequence / choice / all ------------------------------------
    sequence = elem.find(f'{XS}sequence')
    choice   = elem.find(f'{XS}choice')
    all_el   = elem.find(f'{XS}all')
    if sequence is not None:
        content = sequence
    elif choice is not None:
        content = choice
    elif all_el is not None:
        content = all_el
    else:
        content = None

    if content is None:
        return {'type': 'object'}

    is_choice = choice is not None
    properties, required = parse_content_elements(content, is_choice)

    if not properties:
        return {'type': 'object'}

    schema: dict = {}
    if required and not is_choice:
        schema['required'] = sorted(required)
    schema['type'] = 'object'
    schema['properties'] = dict(sorted(properties.items()))
    return schema


# ---------------------------------------------------------------------------
# XSD file parser
# ---------------------------------------------------------------------------

def parse_xsd_file(xsd_path: str):
    """
    Parse an XSD file.

    Returns:
        document_schema : OpenAPI schema for the root Document type,
                          which becomes APIRequest or APIResponse.
        named_schemas   : dict mapping all other type names to their schemas.
    """
    tree = ET.parse(xsd_path)
    root = tree.getroot()

    document_schema = None
    named_schemas: dict = {}

    for child in root:
        local = child.tag.replace(XS, '')
        name  = child.get('name')
        if not name:
            continue

        if local == 'complexType':
            schema = convert_complex_type(child)
            if name == 'Document':
                document_schema = schema
            else:
                named_schemas[name] = schema

        elif local == 'simpleType':
            named_schemas[name] = convert_simple_type(child)

        # Skip root-level xs:element (the <xs:element name="Document" type="Document"/> entry)

    return document_schema, named_schemas


# ---------------------------------------------------------------------------
# OpenAPI spec builder
# ---------------------------------------------------------------------------

DEFAULT_SERVER_URL = 'https://api.example.com/v1'
DEFAULT_TOKEN_URL  = 'https://api.example.com/oauth/token'


def build_openapi(message_name: str,
                  req_xsd_name: str,
                  resp_xsd_name: str,
                  req_document: dict,
                  resp_document: dict,
                  merged_schemas: dict,
                  message_set_name: str = 'MessageSet',
                  server_url: str = DEFAULT_SERVER_URL,
                  token_url: str = DEFAULT_TOKEN_URL) -> dict:
    """Build a complete OpenAPI 3.0.1 specification dict."""

    scope = f'api.example.{message_name}.{message_set_name}'

    # ------------------------------------------------------------------ #
    # Fixed / template schemas
    # ------------------------------------------------------------------ #
    component_schemas: dict = {}

    component_schemas['APIRequest']  = req_document  or {'type': 'object'}
    component_schemas['APIResponse'] = resp_document or {'type': 'object'}

    component_schemas['CorrelationId'] = {
        'maxLength': 36,
        'type': 'string',
        'description': 'Unique identifier that can be used to co-relate request and responses',
        'format': 'uuid',
    }

    component_schemas['ErrorCode'] = {
        'type': 'string',
        'description': (
            'The code indicating the type of error.  Possible values include:\n'
            '* RJCT_FRMT - Rejected - format error. Indicates rejection when the request data is incorrectly formatted.\n'
            '* RJCT_NFND - Rejected - not found. Indicates that the resource or information associated with the resource was not found.\n'
            '* RJCT_SYS - Rejected - system error. Indicates that the request was rejected because of a system error.\n'
            '* RJCT_TOUT - Rejected - timeout. Indicates a timeout in the resource session processing.\n'
            '* RJCT_AUTHZ - Rejected - authorization failed. Indicates that the identity in context is not authorized to access the resource.\n'
            '* RJCT_DPLCT - Rejected - application error. Indicates that duplicate record is being added for the entity.\n'
            '* RJCT_HTTP - Rejected - http error. Indicates that error while performing http opertions.'
        ),
    }

    component_schemas['ErrorMessage'] = {
        'type': 'string',
        'description': 'Error message returned by the service',
    }

    component_schemas['ErrorCodeAndMessage'] = {
        'required': ['code', 'message'],
        'type': 'object',
        'properties': {
            'code':      {'$ref': '#/components/schemas/ErrorCode'},
            'message':   {'$ref': '#/components/schemas/ErrorMessage'},
            'fieldName': {
                'type': 'string',
                'description': 'Location/Field where the error occurred',
            },
        },
    }

    component_schemas['ErrorResponse'] = {
        'required': ['code', 'message'],
        'type': 'object',
        'properties': {
            'code':          {'$ref': '#/components/schemas/ErrorCode'},
            'message':       {'$ref': '#/components/schemas/ErrorMessage'},
            'correlationId': {'$ref': '#/components/schemas/CorrelationId'},
            'details': {
                'type': 'array',
                'items': {'$ref': '#/components/schemas/ErrorCodeAndMessage'},
            },
        },
    }

    for code in ('400', '401', '403', '404', '500'):
        component_schemas[f'Error{code}'] = {
            'allOf': [{'$ref': '#/components/schemas/ErrorResponse'}]
        }

    # XSD-derived schemas – alphabetically sorted, no overwrite of fixed ones
    for k in sorted(merged_schemas.keys()):
        if k not in component_schemas:
            component_schemas[k] = merged_schemas[k]

    # ------------------------------------------------------------------ #
    # Full spec
    # ------------------------------------------------------------------ #
    spec = {
        'openapi': '3.0.1',
        'info': {
            'title': message_name,
            'description': (
                f'{message_set_name} Online {message_name} and Response based on '
                f'{req_xsd_name} and {resp_xsd_name}'
            ),
            'contact': {},
            'version': '1.0',
        },
        'servers': [{'url': server_url}],
        'paths': {
            f'/{message_name}': {
                'post': {
                    'tags': [message_set_name],
                    'description': f'Create {message_name}',
                    'operationId': f'Create {message_name}',
                    'requestBody': {
                        'content': {
                            'application/json': {
                                'schema': {'$ref': '#/components/schemas/APIRequest'},
                            }
                        },
                        'required': False,
                    },
                    'responses': {
                        '200': {
                            'description': 'API request processed successfully',
                            'content': {
                                'application/json': {
                                    'schema': {'$ref': '#/components/schemas/APIResponse'},
                                }
                            },
                        },
                        '400': {
                            'description': 'Bad Request',
                            'content': {
                                'application/json': {
                                    'schema': {'$ref': '#/components/schemas/Error400'},
                                }
                            },
                        },
                        '401': {
                            'description': 'Unauthorized',
                            'content': {
                                'application/json': {
                                    'schema': {'$ref': '#/components/schemas/Error401'},
                                }
                            },
                        },
                        '403': {
                            'description': 'Forbidden',
                            'content': {
                                'application/json': {
                                    'schema': {'$ref': '#/components/schemas/Error403'},
                                }
                            },
                        },
                        '404': {
                            'description': 'Not Found',
                            'content': {
                                'application/json': {
                                    'schema': {'$ref': '#/components/schemas/Error404'},
                                }
                            },
                        },
                        '500': {
                            'description': 'Internal Server Error',
                            'content': {
                                'application/json': {
                                    'schema': {'$ref': '#/components/schemas/Error500'},
                                }
                            },
                        },
                        'default': {
                            'description': 'Unexpected error',
                            'content': {
                                'application/json': {
                                    'schema': {'$ref': '#/components/schemas/ErrorResponse'},
                                }
                            },
                        },
                    },
                    'security': [{'OauthSecurity': [scope]}],
                }
            }
        },
        'components': {
            'schemas': component_schemas,
            'securitySchemes': {
                'OauthSecurity': {
                    'type': 'oauth2',
                    'flows': {
                        'clientCredentials': {
                            'tokenUrl': token_url,
                            'scopes': {
                                scope: (
                                    f'{message_set_name} Online Message {message_name} access scope'
                                ),
                            },
                        }
                    },
                }
            },
        },
    }

    return spec


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_job(message_name: str,
            request_xsd: str,
            response_xsd: str,
            output_path: str,
            message_set_name: str = 'MessageSet',
            server_url: str = DEFAULT_SERVER_URL,
            token_url: str = DEFAULT_TOKEN_URL) -> None:
    """Execute a single XSD → OpenAPI conversion job."""

    req_xsd_name  = os.path.splitext(os.path.basename(request_xsd))[0]
    resp_xsd_name = os.path.splitext(os.path.basename(response_xsd))[0]

    print(f'  message_name      = {message_name}')
    print(f'  output_path       = {output_path}')
    print(f'  request_xsd       = {request_xsd}  ({req_xsd_name})')
    print(f'  response_xsd      = {response_xsd}  ({resp_xsd_name})')
    print(f'  message_set_name  = {message_set_name}')
    print(f'  server_url        = {server_url}')
    print(f'  token_url         = {token_url}')

    # ---------- Parse XSDs -------------------------------------------------
    print(f'\n  Parsing request XSD  : {request_xsd}')
    req_document, req_schemas = parse_xsd_file(request_xsd)
    print(f'    {len(req_schemas)} type definitions,  Document/APIRequest: {"found" if req_document else "NOT FOUND"}')

    print(f'  Parsing response XSD : {response_xsd}')
    resp_document, resp_schemas = parse_xsd_file(response_xsd)
    print(f'    {len(resp_schemas)} type definitions,  Document/APIResponse: {"found" if resp_document else "NOT FOUND"}')

    # ---------- Merge (request XSD takes precedence) -----------------------
    conflicts = set(req_schemas) & set(resp_schemas)
    if conflicts:
        semantic_conflicts = [k for k in sorted(conflicts) if req_schemas[k] != resp_schemas[k]]
        identical_count = len(conflicts) - len(semantic_conflicts)
        print(f'\n  {len(conflicts)} type(s) defined in both XSDs — request XSD definition kept.')
        if identical_count:
            print(f'    {identical_count} type(s) are identical in both XSDs (no conflict).')
        if semantic_conflicts:
            print(f'    {len(semantic_conflicts)} type(s) differ between request and response XSD:')
            for k in semantic_conflicts:
                print(f'      CONFLICT: "{k}"')
                print(f'        request : {req_schemas[k]}')
                print(f'        response: {resp_schemas[k]}')

    merged: dict = {}
    merged.update(resp_schemas)   # lower priority
    merged.update(req_schemas)    # higher priority (overwrites)

    # ---------- Build spec -------------------------------------------------
    print('\n  Building OpenAPI 3.0.1 specification ...')
    spec = build_openapi(
        message_name     = message_name,
        req_xsd_name     = req_xsd_name,
        resp_xsd_name    = resp_xsd_name,
        req_document     = req_document,
        resp_document    = resp_document,
        merged_schemas   = merged,
        message_set_name = message_set_name,
        server_url       = server_url,
        token_url        = token_url,
    )

    # ---------- Write YAML -------------------------------------------------
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        yaml.dump(
            spec,
            stream=f,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
            indent=2,
        )

    total = len(spec['components']['schemas'])
    print(f'\n  Done.  Output: {output_path}')
    print(f'  Total schemas in output: {total}')


def run_from_config(config_path: str) -> None:
    """Load a YAML config file and run all jobs defined in it."""
    with open(config_path, encoding='utf-8') as f:
        config = yaml.safe_load(f)

    defaults = config.get('defaults', {})
    default_message_set_name = defaults.get('message_set_name', 'MessageSet')
    default_output_dir       = defaults.get('output_dir', '.')
    default_server_url       = defaults.get('server_url', DEFAULT_SERVER_URL)
    default_token_url        = defaults.get('token_url',  DEFAULT_TOKEN_URL)

    jobs = config.get('jobs', [])
    if not jobs:
        print('No jobs found in config file.', file=sys.stderr)
        sys.exit(1)

    print(f'Config: {config_path}')
    print(f'  defaults.message_set_name = {default_message_set_name}')
    print(f'  defaults.output_dir       = {default_output_dir}')
    print(f'  defaults.server_url       = {default_server_url}')
    print(f'  defaults.token_url        = {default_token_url}')
    print(f'  {len(jobs)} job(s) to process\n')

    for i, job in enumerate(jobs, start=1):
        message_name     = job.get('message_name')
        request_xsd      = job.get('request_xsd')
        response_xsd     = job.get('response_xsd')
        message_set_name = job.get('message_set_name', default_message_set_name)
        server_url       = job.get('server_url',       default_server_url)
        token_url        = job.get('token_url',        default_token_url)

        if not message_name or not request_xsd or not response_xsd:
            print(f'[Job {i}] SKIPPED — missing required field(s): '
                  f'message_name, request_xsd, or response_xsd', file=sys.stderr)
            continue

        # output_path: explicit > auto-derive from output_dir + message_name
        output_path = job.get(
            'output_path',
            os.path.join(default_output_dir, f'{message_name}.yaml'),
        )

        print(f'[Job {i}/{len(jobs)}] {message_name}')
        run_job(
            message_name     = message_name,
            request_xsd      = request_xsd,
            response_xsd     = response_xsd,
            output_path      = output_path,
            message_set_name = message_set_name,
            server_url       = server_url,
            token_url        = token_url,
        )
        print()


def main():
    parser = argparse.ArgumentParser(
        description='Convert ISO20022-like XSD files directly to OpenAPI 3.0.1 YAML.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            'Examples:\n'
            '  # Single conversion via arguments:\n'
            '  python xsd_to_openapi_v2.py MyMessage out.yaml req.xsd resp.xsd\n\n'
            '  # Batch conversion via config file:\n'
            '  python xsd_to_openapi_v2.py --config xsd_to_openapi.config.yaml'
        ),
    )

    # Config-file mode
    parser.add_argument(
        '--config',
        metavar='CONFIG_YAML',
        help='Path to a YAML config file for batch processing. '
             'When provided, all positional arguments are ignored.',
    )

    # Single-run positional arguments (optional when --config is used)
    parser.add_argument('message_name', nargs='?', help='API message name')
    parser.add_argument('output_path',  nargs='?', help='Output YAML file path')
    parser.add_argument('request_xsd',  nargs='?', help='Request XSD file')
    parser.add_argument('response_xsd', nargs='?', help='Response XSD file')
    parser.add_argument(
        '--message-set-name',
        default='MessageSet',
        help='ISO20022-like message set name used as API tag and scope',
    )
    parser.add_argument(
        '--server-url',
        default=DEFAULT_SERVER_URL,
        help=f'API server base URL written into the OpenAPI spec (default: {DEFAULT_SERVER_URL})',
    )
    parser.add_argument(
        '--token-url',
        default=DEFAULT_TOKEN_URL,
        help=f'OAuth2 token endpoint URL (default: {DEFAULT_TOKEN_URL})',
    )

    args = parser.parse_args()

    # ------------------------------------------------------------------ #
    # Config-file mode
    # ------------------------------------------------------------------ #
    if args.config:
        run_from_config(args.config)
        return

    # ------------------------------------------------------------------ #
    # Single-run mode — validate required positional args
    # ------------------------------------------------------------------ #
    missing = [name for name, val in [
        ('message_name', args.message_name),
        ('output_path',  args.output_path),
        ('request_xsd',  args.request_xsd),
        ('response_xsd', args.response_xsd),
    ] if not val]

    if missing:
        parser.error(
            f'The following arguments are required in single-run mode: '
            f'{", ".join(missing)}\n'
            f'(Or use --config <yaml> for batch mode.)'
        )

    run_job(
        message_name     = args.message_name,
        request_xsd      = args.request_xsd,
        response_xsd     = args.response_xsd,
        output_path      = args.output_path,
        message_set_name = args.message_set_name,
        server_url       = args.server_url,
        token_url        = args.token_url,
    )


if __name__ == '__main__':
    main()
