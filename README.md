# XSD to OpenAPI Converter

Convert ISO20022-like XSD files directly to OpenAPI 3.0.1 YAML specifications.

## Overview

This tool transforms a pair of XSD (XML Schema Definition) files—representing request and response messages—into a complete OpenAPI 3.0.1 YAML specification. It's designed to work with ISO20022 financial messaging standards, including card payment messages and other structured data definitions.

## Features

- **Direct XSD to OpenAPI conversion**: Automatically map XSD elements, types, and constraints to OpenAPI components
- **Request-response pairs**: Handle paired XSD files to generate complete API specifications
- **Batch processing**: Configure multiple message conversions in a single configuration file
- **OAuth2 support**: Include OAuth2 authentication in generated API specs
- **Custom API tags**: Organize operations by message set names
- **Flexible output**: Single file conversion or batch processing with configurable output paths

## Installation

### Requirements
- Python 3.6+
- PyYAML

### Setup

```bash
# Clone the repository
git clone <repository-url>
cd xsd-to-openapi

# Install dependencies
pip install pyyaml
```

## Usage

### Single File Conversion

Convert a pair of XSD files to OpenAPI YAML:

```bash
python xsd_to_openapi.py <message_name> <output_path> <request_xsd> <response_xsd>
```

**Example:**
```bash
python xsd_to_openapi.py AuthorisationInitiation output/AuthorisationInitiation.yaml \
    sample/cain.001.001.03.xsd sample/cain.002.001.03.xsd
```

### Batch Processing

Use a configuration file for batch conversions:

```bash
python xsd_to_openapi.py --config xsd_to_openapi.config.yaml
```

#### Configuration File Format

Create a `xsd_to_openapi.config.yaml` file:

```yaml
defaults:
  message_set_name: Acquirer to Issuer Card Messages    # API tag name
  output_dir: ./output                                  # Default output directory
  server_url: https://api.example.com/v1               # Optional: API server URL
  token_url: https://api.example.com/oauth/token       # Optional: OAuth2 token endpoint

jobs:
  - message_name: AuthorisationInitiation
    request_xsd: sample/cain.001.001.03.xsd
    response_xsd: sample/cain.002.001.03.xsd
    # output_path defaults to: ./output/AuthorisationInitiation.yaml

  - message_name: AcceptorCompletionAdvice
    request_xsd: sample/caaa.003.001.11.xsd
    response_xsd: sample/caaa.004.001.10.xsd
    output_path: ./output/CancellationAdvice.yaml      # Override default path
    message_set_name: "Card Payments Exchange"         # Override default tag
```

## Output

The tool generates a complete OpenAPI 3.0.1 specification including:

- **Paths**: Request and response endpoints based on message names
- **Schemas**: Component definitions mapping XSD types to OpenAPI schemas
- **Responses**: Structured response definitions with status codes
- **Security**: OAuth2 configuration (if configured)
- **Tags**: Message set organization

### Example Output

```yaml
openapi: 3.0.1
info:
  title: Card Payment Messages
  version: 1.0.0
servers:
  - url: https://api.example.com/v1
paths:
  /AuthorisationInitiation:
    post:
      summary: Authorisation Initiation
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/AuthorisationInitiation'
      responses:
        '200':
          description: Success
components:
  schemas:
    AuthorisationInitiation:
      type: object
      properties:
        # ... properties derived from XSD
  securitySchemes:
    OAuth2:
      type: oauth2
      flows:
        clientCredentials:
          tokenUrl: https://api.example.com/oauth/token
```

## Type Mappings

### XSD to OpenAPI Type Mappings

| XSD Type | OpenAPI Type |
|----------|--------------|
| xs:string, xs:token, xs:anyURI, xs:base64Binary | string |
| xs:integer, xs:long, xs:int, xs:short, xs:byte | integer |
| xs:decimal, xs:float, xs:double | number |
| xs:boolean | boolean |
| xs:date, xs:dateTime, xs:time | string (ISO 8601) |
| Custom types | $ref to components/schemas |

## Directory Structure

```
xsd-to-openapi/
├── xsd_to_openapi.py                      # Main converter script
├── sample/
│   ├── cain.001.001.03.xsd               # Example ISO20022 request message
│   ├── cain.002.001.03.xsd               # Example ISO20022 response message
│   ├── caaa.003.001.11.xsd               # Example alternative request
│   ├── caaa.004.001.10.xsd               # Example alternative response
│   ├── xsd_to_openapi.config.sample.yaml # Sample configuration file
│   └── output/
│       └── AuthorisationInitiation.yaml  # Generated OpenAPI spec
├── README.md                              # This file
└── LICENSE                                # License file
```

## Examples

### Basic Conversion

```bash
python xsd_to_openapi.py MyMessage output.yaml request.xsd response.xsd
```

### Batch Processing with Server Configuration

Create `config.yaml`:

```yaml
defaults:
  message_set_name: Financial Messages
  output_dir: ./specs
  server_url: https://banking-api.example.com
  token_url: https://auth.example.com/oauth/token

jobs:
  - message_name: PaymentInitiation
    request_xsd: messages/pain.001.001.03.xsd
    response_xsd: messages/pain.002.001.03.xsd
```

Then run:
```bash
python xsd_to_openapi.py --config config.yaml
```

## Development

### Code Structure

- **Type conversion functions**: Handle primitive and complex type mappings
- **XSD parsing**: Process XML schema elements and attributes
- **OpenAPI schema generation**: Build compliant OpenAPI 3.0.1 structures
- **YAML output**: Generate properly formatted specification files

## License

See [LICENSE](LICENSE) for details.

## Contributing

Contributions are welcome! Please ensure:
- Code follows PEP 8 style guidelines
- Generated OpenAPI specs validate against OpenAPI 3.0.1 schema
- XSD samples are included for testing new features

## Support

For issues, questions, or suggestions, please open an issue in the repository.
