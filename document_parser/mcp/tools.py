"""
MCP tool definitions.
"""

import mcp.types as types


def get_tool_definitions() -> list[types.Tool]:
    """
    Get MCP tool definitions.

    Returns:
        List of Tool definitions
    """
    return [
        types.Tool(
            name="parse_document",
            description=(
                "Parse a document and extract its content as Markdown. Accepts three "
                "input modes: (1) source: local file path, (2) url: HTTP/HTTPS URL to "
                "download, (3) content: base64-encoded file content with filename. "
                "For files uploaded by users or files not on the parser's local "
                "filesystem, use the content parameter with base64-encoded bytes and "
                "provide the filename."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "Local file path on the parser's filesystem. Only works if the file exists locally on the parser service.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Base64-encoded file content. Use this when the file is not on the parser's local filesystem (e.g., files uploaded by users to agents). Mutually exclusive with source.",
                    },
                    "filename": {
                        "type": "string",
                        "description": "Original filename with extension (e.g., 'document.docx'). Required when using content parameter.",
                    },
                    "pipeline": {
                        "type": "string",
                        "enum": ["standard", "vlm", "asr", "auto"],
                        "description": "Processing pipeline (optional, auto-detected if not specified)",
                    },
                    "options": {
                        "type": "object",
                        "description": "Additional processing options",
                        "properties": {
                            "ocr_enabled": {
                                "type": "boolean",
                                "description": "Enable OCR for scanned documents",
                            },
                            "ocr_language": {
                                "type": "string",
                                "description": "OCR language code (e.g., 'eng', 'spa')",
                            },
                            "table_accuracy_mode": {
                                "type": "string",
                                "enum": ["fast", "accurate"],
                                "description": "Table extraction accuracy",
                            },
                            "pdf_backend": {
                                "type": "string",
                                "enum": ["dlparse_v4", "pypdfium2"],
                                "description": "PDF processing backend",
                            },
                            "enable_enrichments": {
                                "type": "boolean",
                                "description": "Enable code/formula enrichments",
                            },
                        },
                    },
                },
                "oneOf": [
                    {"required": ["source"]},
                    {"required": ["content", "filename"]},
                ],
            },
        ),
        types.Tool(
            name="parse_document_from_url",
            description=(
                "Download a document from an HTTP/HTTPS URL (R2 presigned, public "
                "link, etc.) and parse it to Markdown. Use this when the file is "
                "available at a URL the parser can reach but is not on the parser's "
                "local filesystem. For base64-encoded content from user uploads, "
                "use parse_document with the content parameter instead."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "HTTP/HTTPS URL pointing to the document (presigned R2 URL, public link, etc.). The parser downloads the file then parses it.",
                    },
                    "filename_hint": {
                        "type": "string",
                        "description": "Optional filename with extension to preserve format detection when the URL has no obvious filename (e.g., 'report.docx').",
                    },
                    "pipeline": {
                        "type": "string",
                        "enum": ["standard", "vlm", "asr", "auto"],
                        "description": "Processing pipeline (optional, auto-detected if not specified)",
                    },
                    "options": {
                        "type": "object",
                        "description": "Additional processing options (same as parse_document.options)",
                    },
                },
                "required": ["url"],
            },
        ),
        types.Tool(
            name="parse_document_advanced",
            description="Advanced document parsing with detailed configuration options",
            inputSchema={
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "File path or URL to the document",
                    },
                    "pipeline": {
                        "type": "string",
                        "enum": ["standard", "vlm", "asr"],
                        "description": "Processing pipeline",
                    },
                    "ocr_enabled": {
                        "type": "boolean",
                        "description": "Enable/disable OCR",
                    },
                    "ocr_language": {
                        "type": "string",
                        "description": "OCR language code (e.g., 'eng,spa')",
                    },
                    "table_accuracy_mode": {
                        "type": "string",
                        "enum": ["fast", "accurate"],
                        "description": "Table extraction mode",
                    },
                    "pdf_backend": {
                        "type": "string",
                        "enum": ["dlparse_v4", "pypdfium2"],
                        "description": "PDF backend to use",
                    },
                    "enable_enrichments": {
                        "type": "boolean",
                        "description": "Enable enrichments",
                    },
                },
                "required": ["source"],
            },
        ),
        types.Tool(
            name="get_job_status",
            description="Get the status of a processing job",
            inputSchema={
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description": "Job identifier",
                    }
                },
                "required": ["job_id"],
            },
        ),
        types.Tool(
            name="list_supported_formats",
            description="List all supported input formats and processing pipelines",
            inputSchema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name="get_queue_statistics",
            description="Get current queue status and processing statistics",
            inputSchema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        ),
    ]
