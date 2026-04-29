"""
MCP tool handlers.
"""

import base64
import binascii
import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import mcp.types as types

from document_parser.config.models import ApplicationSettings
from document_parser.core.exceptions import ProcessingError
from document_parser.engine.processor import DocumentProcessor
from document_parser.processing.job import Job, ProcessingPipeline
from document_parser.processing.task_queue import TaskQueue
from document_parser.processing.task_tracker import TaskTracker
from document_parser.utils.file_utils import ensure_directory, sanitize_filename
from document_parser.utils.network_utils import (
    extract_filename_from_url,
    validate_url_scheme,
)
from document_parser.utils.system_utils import generate_unique_id


class ToolHandlers:
    """
    Handlers for MCP tool calls.
    """

    def __init__(
        self,
        settings: ApplicationSettings,
        processor: DocumentProcessor,
        task_queue: TaskQueue,
        task_tracker: TaskTracker,
    ):
        """
        Initialize tool handlers.

        Args:
            settings: Application settings
            processor: Document processor
            task_queue: Task queue
            task_tracker: Task tracker
        """
        self.settings = settings
        self.processor = processor
        self.task_queue = task_queue
        self.task_tracker = task_tracker
        self._logger = logging.getLogger(__name__)

    async def handle_parse_document(
        self, arguments: dict[str, Any]
    ) -> list[types.TextContent]:
        """
        Handle basic document parsing request.

        Accepts either `source` (local path / reachable URL) or `content`
        (base64-encoded bytes) + `filename`. The two modes are mutually
        exclusive — content mode writes to a temp file, parses it, and
        cleans up regardless of outcome.

        Args:
            arguments: Tool arguments

        Returns:
            List of TextContent with result
        """
        source = arguments.get("source")
        content_b64 = arguments.get("content")
        filename = arguments.get("filename")

        if source and content_b64:
            raise ValueError(
                "Parameters `source` and `content` are mutually exclusive — provide one"
            )
        if not source and not content_b64:
            raise ValueError("Missing required parameter: provide `source` or `content`")
        if content_b64 and not filename:
            raise ValueError("Parameter `filename` is required when `content` is provided")

        pipeline = arguments.get("pipeline", "auto")
        options = arguments.get("options", {})

        temp_path: Path | None = None
        if content_b64:
            try:
                file_bytes = base64.b64decode(content_b64, validate=True)
            except (binascii.Error, ValueError) as e:
                raise ValueError(f"Invalid base64 content: {e}")

            max_bytes = self.settings.storage.max_file_size_mb * 1024 * 1024
            if len(file_bytes) > max_bytes:
                raise ProcessingError(
                    f"Decoded content exceeds max_file_size_mb "
                    f"({self.settings.storage.max_file_size_mb} MB)"
                )

            safe_name = sanitize_filename(filename) or "document"
            if not Path(safe_name).suffix:
                safe_name += ".bin"

            temp_dir = ensure_directory(self.settings.storage.temp_directory)
            unique_id = generate_unique_id("inline")
            temp_path = temp_dir / f"{unique_id}_{safe_name}"

            try:
                with open(temp_path, "wb") as fh:
                    fh.write(file_bytes)
            except OSError as e:
                raise ProcessingError(f"Failed to write inline content to disk: {e}")

            source = str(temp_path)
            self._logger.info(
                f"Wrote {len(file_bytes)} inline bytes to {temp_path}"
            )

        self._logger.info(f"Parsing document: {source}")

        try:
            # Create job
            job_id = generate_unique_id("job")
            pipeline_enum = self._parse_pipeline_string(pipeline)

            job = Job(
                job_id=job_id,
                source_path=source,
                pipeline=pipeline_enum,
                options=options,
            )

            # Register and queue job
            self.task_tracker.register_job(job)
            queued = await self.task_queue.enqueue(job)

            if not queued:
                raise ProcessingError("Queue is full, please try again later")

            # Process job
            job = await self.task_queue.dequeue()
            if not job:
                raise ProcessingError("Failed to retrieve job from queue")

            self.task_tracker.mark_active(job.job_id)

            try:
                # Process document
                markdown_result = await self.processor.process_document(
                    job.source_path, job.pipeline.value, job.options
                )

                # Mark completed
                job.mark_completed(markdown_result)
                self.task_tracker.mark_inactive(job.job_id)

                return [types.TextContent(type="text", text=markdown_result)]

            except Exception as e:
                job.mark_failed(str(e))
                self.task_tracker.mark_inactive(job.job_id)
                raise

        except ProcessingError:
            raise

        except Exception as e:
            self._logger.error(f"Error parsing document: {e}")
            raise ProcessingError(f"Document parsing failed: {str(e)}")

        finally:
            if temp_path is not None:
                try:
                    if temp_path.exists():
                        temp_path.unlink()
                except Exception as cleanup_err:
                    self._logger.warning(
                        f"Failed to remove inline temp file {temp_path}: {cleanup_err}"
                    )

    async def handle_parse_document_from_url(
        self, arguments: dict[str, Any]
    ) -> list[types.TextContent]:
        """
        Download a document from a URL into a temp file then parse it.

        Used by remote clients (e.g., agents on a different host) that cannot
        share a local filesystem path with the MCP server.
        """
        url = arguments.get("url")
        if not url:
            raise ValueError("Missing required parameter: url")

        allowed_schemes = self.settings.storage.allowed_schemes
        if not validate_url_scheme(url, allowed_schemes):
            raise ValueError(
                f"URL scheme not allowed. Allowed schemes: {allowed_schemes}"
            )

        pipeline = arguments.get("pipeline", "auto")
        options = arguments.get("options", {})
        filename_hint = arguments.get("filename_hint")

        # Resolve a safe local filename
        url_filename = extract_filename_from_url(url)
        chosen = filename_hint or url_filename or "document"
        # Drop any query string artefacts that may have leaked into the path
        chosen = chosen.split("?", 1)[0]
        safe_name = sanitize_filename(chosen) or "document"

        # If no extension at all, default to .bin so docling can sniff it
        if not Path(safe_name).suffix:
            safe_name += ".bin"

        temp_dir = ensure_directory(self.settings.storage.temp_directory)
        unique_id = generate_unique_id("dl")
        temp_path = temp_dir / f"{unique_id}_{safe_name}"

        timeout = self.settings.storage.download_timeout_seconds
        max_bytes = self.settings.storage.max_file_size_mb * 1024 * 1024

        self._logger.info(f"Downloading {url} -> {temp_path}")

        try:
            downloaded = 0
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(timeout, connect=30.0),
                follow_redirects=True,
            ) as client:
                async with client.stream("GET", url) as response:
                    response.raise_for_status()
                    with open(temp_path, "wb") as fh:
                        async for chunk in response.aiter_bytes(chunk_size=65536):
                            downloaded += len(chunk)
                            if downloaded > max_bytes:
                                raise ProcessingError(
                                    f"Download exceeds max_file_size_mb "
                                    f"({self.settings.storage.max_file_size_mb} MB)"
                                )
                            fh.write(chunk)

            self._logger.info(
                f"Downloaded {downloaded} bytes for {urlparse(url).netloc}"
            )

            return await self.handle_parse_document(
                {
                    "source": str(temp_path),
                    "pipeline": pipeline,
                    "options": options,
                }
            )

        except httpx.HTTPError as e:
            raise ProcessingError(f"Failed to download {url}: {e}")

        finally:
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except Exception as cleanup_err:
                self._logger.warning(
                    f"Failed to remove temp file {temp_path}: {cleanup_err}"
                )

    async def handle_parse_document_advanced(
        self, arguments: dict[str, Any]
    ) -> list[types.TextContent]:
        """
        Handle advanced document parsing request.

        Args:
            arguments: Tool arguments

        Returns:
            List of TextContent with result
        """
        source = arguments.get("source")
        if not source:
            raise ValueError("Missing required parameter: source")

        pipeline = arguments.get("pipeline", "standard")

        # Extract advanced options
        options = {
            "ocr_enabled": arguments.get("ocr_enabled"),
            "ocr_language": arguments.get("ocr_language"),
            "table_accuracy_mode": arguments.get("table_accuracy_mode"),
            "pdf_backend": arguments.get("pdf_backend"),
            "enable_enrichments": arguments.get("enable_enrichments", False),
        }

        # Remove None values
        options = {k: v for k, v in options.items() if v is not None}

        self._logger.info(f"Advanced parsing: {source} with pipeline: {pipeline}")

        # Use same logic as basic parsing
        arguments_copy = {
            "source": source,
            "pipeline": pipeline,
            "options": options,
        }

        return await self.handle_parse_document(arguments_copy)

    async def handle_get_job_status(
        self, arguments: dict[str, Any]
    ) -> list[types.TextContent]:
        """
        Handle job status request.

        Args:
            arguments: Tool arguments

        Returns:
            List of TextContent with status
        """
        job_id = arguments.get("job_id")
        if not job_id:
            raise ValueError("Missing required parameter: job_id")

        job = self.task_tracker.get_job(job_id)
        if not job:
            raise ValueError(f"Job not found: {job_id}")

        status_data = job.to_dict()

        return [types.TextContent(type="text", text=json.dumps(status_data, indent=2))]

    async def handle_list_supported_formats(
        self, arguments: dict[str, Any]
    ) -> list[types.TextContent]:
        """
        Handle supported formats request.

        Args:
            arguments: Tool arguments

        Returns:
            List of TextContent with formats
        """
        formats = self.processor.get_supported_formats()

        return [types.TextContent(type="text", text=json.dumps(formats, indent=2))]

    async def handle_get_queue_statistics(
        self, arguments: dict[str, Any]
    ) -> list[types.TextContent]:
        """
        Handle queue statistics request.

        Args:
            arguments: Tool arguments

        Returns:
            List of TextContent with statistics
        """
        queue_stats = self.task_queue.get_stats()
        tracker_stats = self.task_tracker.get_statistics()

        combined_stats = {
            "queue": queue_stats,
            "processing": tracker_stats,
        }

        return [
            types.TextContent(type="text", text=json.dumps(combined_stats, indent=2))
        ]

    def _parse_pipeline_string(self, pipeline: str) -> ProcessingPipeline:
        """
        Parse pipeline string to enum.

        Args:
            pipeline: Pipeline name

        Returns:
            ProcessingPipeline enum
        """
        pipeline_map = {
            "standard": ProcessingPipeline.STANDARD,
            "vlm": ProcessingPipeline.VLM,
            "asr": ProcessingPipeline.ASR,
            "auto": ProcessingPipeline.AUTO,
        }

        return pipeline_map.get(pipeline.lower(), ProcessingPipeline.AUTO)
