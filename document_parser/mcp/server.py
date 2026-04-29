"""
Main MCP server implementation.
"""

import asyncio
import logging
from typing import Any

import mcp.server.stdio
import mcp.types as types
from mcp.server import Server

from document_parser.config.models import ApplicationSettings
from document_parser.engine.processor import DocumentProcessor
from document_parser.mcp.handlers import ToolHandlers
from document_parser.mcp.tools import get_tool_definitions
from document_parser.processing.task_queue import TaskQueue
from document_parser.processing.task_tracker import TaskTracker
from document_parser.utils.file_utils import cleanup_old_files


class DocumentParserServer:
    """
    MCP server for document parsing and processing.
    """

    def __init__(self, settings: ApplicationSettings):
        """
        Initialize MCP server.

        Args:
            settings: Application settings
        """
        self.settings = settings
        self._logger = logging.getLogger(__name__)

        # Initialize components
        self.processor = DocumentProcessor(settings)
        self.task_queue = TaskQueue(max_size=settings.server.max_concurrent_jobs)
        self.task_tracker = TaskTracker(max_history=100)

        # Initialize tool handlers
        self.handlers = ToolHandlers(
            settings=settings,
            processor=self.processor,
            task_queue=self.task_queue,
            task_tracker=self.task_tracker,
        )

        # Create MCP server
        self.server = Server(settings.server.name)
        self._register_handlers()
        self._start_background_tasks()

    def _register_handlers(self) -> None:
        """Register MCP tool handlers."""

        @self.server.list_tools()
        async def handle_list_tools():
            """List available tools."""
            return get_tool_definitions()

        @self.server.call_tool()
        async def handle_call_tool(name: str, arguments: dict[str, Any]):
            """Handle tool calls."""
            try:
                if name == "parse_document":
                    return await self.handlers.handle_parse_document(arguments)
                elif name == "parse_document_from_url":
                    return await self.handlers.handle_parse_document_from_url(
                        arguments
                    )
                elif name == "parse_document_advanced":
                    return await self.handlers.handle_parse_document_advanced(arguments)
                elif name == "get_job_status":
                    return await self.handlers.handle_get_job_status(arguments)
                elif name == "list_supported_formats":
                    return await self.handlers.handle_list_supported_formats(arguments)
                elif name == "get_queue_statistics":
                    return await self.handlers.handle_get_queue_statistics(arguments)
                else:
                    raise ValueError(f"Unknown tool: {name}")

            except Exception as e:
                self._logger.error(f"Tool call error ({name}): {e}")
                return [types.TextContent(type="text", text=f"Error: {str(e)}")]

    def _start_background_tasks(self) -> None:
        """Start background maintenance tasks."""

        async def cleanup_task():
            """Periodic cleanup of temporary files and old jobs."""
            while True:
                try:
                    # Cleanup temp files
                    cleanup_old_files(
                        self.settings.storage.temp_directory,
                        self.settings.storage.cleanup_interval_hours,
                    )

                    # Cleanup old job history
                    # (Task tracker handles this automatically)

                    # Wait before next cleanup (1 hour)
                    await asyncio.sleep(3600)

                except Exception as e:
                    self._logger.error(f"Cleanup task error: {e}")
                    await asyncio.sleep(300)  # Wait 5 minutes on error

        # Start cleanup task
        asyncio.create_task(cleanup_task())

    async def run(self) -> None:
        """Run the MCP server."""
        self._logger.info(
            f"Starting {self.settings.server.name} v{self.settings.server.version}"
        )

        # Use stdio transport
        async with mcp.server.stdio.stdio_server() as (
            read_stream,
            write_stream,
        ):
            init_options = self.server.create_initialization_options()
            await self.server.run(read_stream, write_stream, init_options)
