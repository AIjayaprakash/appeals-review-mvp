"""Quick standalone check that AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_API_KEY (from
.env or the shell environment) actually authenticate against a real deployment --
same client construction the app itself uses (app.core.nodes.get_client), so a
pass here means the running app's intake/summarization/evaluation calls will work.

Run: python check_azure_openai.py
"""

from dotenv import load_dotenv

load_dotenv()

from app.core.nodes import AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, INTAKE_MODEL_DEPLOYMENT, get_client


def main() -> int:
    if not AZURE_OPENAI_ENDPOINT or not AZURE_OPENAI_API_KEY:
        print("FAIL: AZURE_OPENAI_ENDPOINT and/or AZURE_OPENAI_API_KEY is not set.")
        return 1

    print(f"Endpoint:   {AZURE_OPENAI_ENDPOINT}")
    print(f"Deployment: {INTAKE_MODEL_DEPLOYMENT}")
    print("Calling Azure OpenAI...")

    try:
        client = get_client()
        response = client.chat.completions.create(
            model=INTAKE_MODEL_DEPLOYMENT,
            messages=[{"role": "user", "content": "Reply with exactly: OK"}],
            max_tokens=5,
        )
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}")
        return 1

    reply = response.choices[0].message.content
    print(f"Model replied: {reply!r}")
    print("PASS: credentials and deployment are working.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
