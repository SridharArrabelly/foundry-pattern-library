"""
Shared environment + client factory. One place to load .env and build the two
clients every pattern needs:

  * project_client()  -> Foundry project client (agents, evals, tracing)
  * gateway_client()  -> an OpenAI-compatible client pointed at the customer's
                          Azure AI Gateway (APIM) — their LiteLLM analogue.

Auth is keyless-first: `az login` + DefaultAzureCredential. Set keys in .env only
if you prefer key-based auth.
"""
import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


def env(name: str, default: str | None = None, required: bool = False) -> str | None:
    val = os.environ.get(name, default)
    if required and not val:
        raise SystemExit(f"Missing required env var: {name} (set it in .env)")
    return val


# ----- Foundry project (Patterns 2, 6, 7) -----------------------------------
PROJECT_ENDPOINT = env("PROJECT_ENDPOINT")
MODEL_DEPLOYMENT_NAME = env("MODEL_DEPLOYMENT_NAME", "gpt-5.4-mini")

# The Foundry AI Services *account* host (drop the /api/projects/<name> suffix).
# It's a multi-service account, so Content Safety (Prompt Shields) lives here too —
# same resource, same keyless Entra auth (Pattern 8).
FOUNDRY_ACCOUNT_ENDPOINT = (
    PROJECT_ENDPOINT.split("/api/projects/")[0] if PROJECT_ENDPOINT else None
)
COGNITIVE_SERVICES_SCOPE = "https://cognitiveservices.azure.com/.default"


@lru_cache
def project_client():
    """Foundry project client using DefaultAzureCredential (az login)."""
    from azure.identity import DefaultAzureCredential
    from azure.ai.projects import AIProjectClient

    if not PROJECT_ENDPOINT:
        raise SystemExit("Set PROJECT_ENDPOINT in .env")
    return AIProjectClient(
        endpoint=PROJECT_ENDPOINT, credential=DefaultAzureCredential()
    )


# ----- Azure AI Gateway / APIM (Pattern 1, and BYOM narration) --------------
# Two APIs exist on the same APIM (added from the Foundry portal):
#   * /your-foundry-resource  -> per-project APIM SUBSCRIPTION KEY (api-key header)
#   * /your-project            -> KEYLESS Microsoft Entra ID (validate-azure-ad-token)
# We default to the KEYLESS Entra endpoint — banks don't want static keys. Set
# GATEWAY_KEY only to fall back to the subscription-key API.
GATEWAY_ENDPOINT = env(
    "GATEWAY_ENDPOINT",
    "https://your-apim-gateway.azure-api.net/your-project",
)
# Leave blank for Entra ID (keyless). Set it to use the subscription-key API,
# in which case AzureOpenAI sends it in the `api-key` header APIM expects.
GATEWAY_KEY = env("GATEWAY_KEY")
GATEWAY_MODEL = env("GATEWAY_MODEL", "gpt-5.4-mini")
GATEWAY_API_VERSION = env("GATEWAY_API_VERSION", "2024-10-21")
# Agent Framework's OpenAIChatClient targets the newer /openai/v1/ surface, which
# only accepts the literal api-version "preview" (not dated versions).
GATEWAY_V1_API_VERSION = env("GATEWAY_V1_API_VERSION", "preview")
# Entra ID token audience. The /your-project API validates this audience, and
# APIM reaches the AOAI backend with its own managed identity (keyless end to end).
GATEWAY_TOKEN_SCOPE = env(
    "GATEWAY_TOKEN_SCOPE", "https://cognitiveservices.azure.com/.default"
)


@lru_cache
def gateway_client():
    """
    An Azure-OpenAI-compatible client pointed at the customer's Azure AI Gateway
    (APIM). Keyless-first, which is what a bank wants:

      1. Default (GATEWAY_KEY blank) -> Microsoft Entra ID via DefaultAzureCredential.
         Hits the /your-project API, whose validate-azure-ad-token policy
         enforces a valid Entra token (no keys accepted). APIM then reaches the
         AOAI backend with its own managed identity — keyless end to end.
      2. Fallback (GATEWAY_KEY set) -> APIM subscription key in the `api-key`
         header, against the per-project /your-foundry-resource API.

    This is the whole point of Pattern 1: apps keep calling ONE endpoint through
    the gateway; Foundry is just another provider behind it. `model=` is the
    Foundry deployment name the gateway routes to.
    """
    from openai import AzureOpenAI

    endpoint = GATEWAY_ENDPOINT.rstrip("/")
    if GATEWAY_KEY:  # APIM subscription key -> sent as the `api-key` header
        return AzureOpenAI(
            azure_endpoint=endpoint,
            api_key=GATEWAY_KEY,
            api_version=GATEWAY_API_VERSION,
        )
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider

    token_provider = get_bearer_token_provider(
        DefaultAzureCredential(), GATEWAY_TOKEN_SCOPE
    )
    return AzureOpenAI(
        azure_endpoint=endpoint,
        azure_ad_token_provider=token_provider,
        api_version=GATEWAY_API_VERSION,
    )


# ----- Tracing / App Insights (Pattern 6) -----------------------------------
def app_insights_connection_string() -> str:
    """
    Resolve the Application Insights connection string used for tracing.

    Order:
      1. APPLICATIONINSIGHTS_CONNECTION_STRING in .env IF it's a real connection
         string (contains 'InstrumentationKey=').
      2. Otherwise ask the Foundry project for the App Insights resource it is
         connected to. This is the SAME resource that backs the portal 'Tracing'
         tab, so spans show up both in the portal and in App Insights.

    Note: a value like '/subscriptions/.../components/appinsights-...' is a
    resource ID, NOT a connection string — we fall through to the project lookup.
    """
    val = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING", "")
    if "InstrumentationKey=" in val:
        return val
    conn = project_client().telemetry.get_application_insights_connection_string()
    if not conn:
        raise SystemExit(
            "The Foundry project has no Application Insights connected. Connect one in "
            "the portal (project -> Tracing -> Application Insights), or paste a real "
            "connection string (InstrumentationKey=...) into APPLICATIONINSIGHTS_CONNECTION_STRING."
        )
    return conn
