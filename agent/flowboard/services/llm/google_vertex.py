"""Google Vertex AI provider — API wrapper around the Vertex AI SDK.

Supports both text-only (e.g., `gemini-pro`) and vision-capable (e.g.,
`gemini-pro-vision`) models. Authentication is handled implicitly by the
Vertex AI SDK using service account credentials or gcloud CLI defaults.

Environment variables:
- `GCP_PROJECT_ID`: The Google Cloud project ID.
- `GCP_LOCATION`: The Google Cloud region (e.g., `us-central1`).
- `FLOWBOARD_VERTEX_MODEL`: Override the default model.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from google.api_core.exceptions import GoogleAPIError
from google.cloud import aiplatform
import vertexai
from vertexai.preview.generative_models import GenerativeModel, Part

from .base import LLMError, LLMProvider

logger = logging.getLogger(__name__)

# Default Vertex AI model names
_DEFAULT_TEXT_MODEL: str = "gemini-pro"
_DEFAULT_VISION_MODEL: str = "gemini-pro-vision"

# Environment variables for configuration
_GCP_PROJECT_ID_ENV: str = "GCP_PROJECT_ID"
_GCP_LOCATION_ENV: str = "GCP_LOCATION"
_FLOWBOARD_VERTEX_MODEL_ENV: str = "FLOWBOARD_VERTEX_MODEL"


class GoogleVertex(LLMProvider):
    """Conforms to ``LLMProvider`` (structural typing)."""

    name: str = "google_vertex"
    supports_vision: bool = True  # Both gemini-pro and gemini-pro-vision are supported
    test_timeout_secs: float = 180.0

    def __init__(self) -> None:
        self._available: Optional[bool] = None
        self._project_id: Optional[str] = os.environ.get(_GCP_PROJECT_ID_ENV)
        self._location: Optional[str] = os.environ.get(_GCP_LOCATION_ENV)
        self._initialized: bool = False

    async def is_available(self) -> bool:
        """Cached check: are GCP_PROJECT_ID and GCP_LOCATION environment variables set?

        This does not verify actual authentication or model availability, just that
        the basic configuration is present to attempt an API call.
        """
        if self._available is None:
            if not self._project_id:
                logger.warning(
                    "GoogleVertex: %s environment variable not set.",
                    _GCP_PROJECT_ID_ENV,
                )
                self._available = False
            elif not self._location:
                logger.warning(
                    "GoogleVertex: %s environment variable not set.",
                    _GCP_LOCATION_ENV,
                )
                self._available = False
            else:
                try:
                    # Attempt a light-weight initialization to check credentials/config
                    # This doesn't call a model, but checks if the SDK can initialize
                    aiplatform.init(
                        project=self._project_id,
                        location=self._location,
                    )
                    self._available = True
                    self._initialized = True
                    logger.info(
                        "GoogleVertex: available with project_id=%s, location=%s",
                        self._project_id,
                        self._location,
                    )
                except Exception as e:
                    logger.warning(
                        "GoogleVertex: initialization failed (check credentials/config): %s",
                        e,
                    )
                    self._available = False
        return self._available

    def reset_cache(self) -> None:
        """Testing hook + Settings panel rescan support."""
        self._available = None
        self._initialized = False

    async def run(
        self,
        user_prompt: str,
        *,
        system_prompt: Optional[str] = None,
        attachments: Optional[list[str]] = None,
        timeout: float = 90.0,
    ) -> str:
        """Invoke Google Vertex AI GenerativeModel and return its response."""
        if not await self.is_available():
            raise LLMError("Google Vertex AI is not available (check configuration).")

        if not self._initialized:
            # Re-initialize if not already, for robustness
            try:
                vertexai.init(
                    project=self._project_id,
                    location=self._location,
                )
                self._initialized = True
            except Exception as e:
                raise LLMError(f"Failed to initialize Vertex AI: {e}") from e

        model_name = os.environ.get(_FLOWBOARD_VERTEX_MODEL_ENV)
        if attachments:
            model = GenerativeModel(model_name or _DEFAULT_VISION_MODEL)
            contents = []
            if system_prompt:
                contents.append(Part.from_text(f"[System: {system_prompt}]\n\n"))
            contents.append(Part.from_text(user_prompt))
            for attachment_path in attachments:
                contents.append(Part.from_file(attachment_path))

"))
        else:
            model = GenerativeModel(model_name or _DEFAULT_TEXT_MODEL)
            contents = []
            if system_prompt:
                contents.append(f"[System: {system_prompt}]\n\n")
            contents.append(user_prompt)

")


        try:
            # The SDK handles timeouts, pass it directly.
            response = await model.generate_content_async(
                contents,
                generation_config={"temperature": 0.0},
                request_options={"timeout": timeout},
            )
            if response.candidates:
                return response.candidates[0].text
            else:
                raise LLMError("Google Vertex AI returned no candidates.")
        except GoogleAPIError as e:
            raise LLMError(f"Google Vertex AI API error: {e}") from e
        except Exception as e:
            raise LLMError(f"Google Vertex AI unexpected error: {e}") from e

