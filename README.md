# Lyrenza Hotel Dallas bad contact-center demo

A standalone FastAPI and Azure Voice Live web app that intentionally
demonstrates poor contact-center behavior.

## Demo behavior

- Plays a short remote-phone ring before connecting.
- Jolene answers with: "Hi, I'm Jolene from Lyrenza Hotel Dallas, how can I help you?"
- Uses `gpt-realtime-2.1` by default with the `coral` voice and a prompted,
  exaggerated Deep South drawl. Callers can switch between GPT Realtime 2.1
  with OpenAI voices and Azure Realtime with Azure native voices before dialing.
- A pool-hours question triggers a six-second tool call. All microphone input
  forwarding and assistant audio playback are suppressed during the lookup.
- After the lookup, Jolene asks: "Are you asking if there's a pool with a nice
  background or scenery?"
- A request for a live person gets the offended, emphatic response: "I AM a
  live person, y'all! I'm Jolene from Lyrenza Hotel Dallas!"
- Ending the call creates a combined caller-and-system recording for download.
  Recording happens in the browser and is not uploaded.

`gpt-realtime-2.1` is currently a preview Voice Live model. Set
`AZURE_VOICELIVE_MODEL` to another supported Voice Live model if it is not
available in the selected resource region.

## Local development

Prerequisites:

- Python 3.13;
- Azure CLI authenticated to an Azure resource with Voice Live access;
- the signed-in identity has permission to call the resource.

Create a virtual environment using an approved package source, install
`requirements-dev.txt`, then set the environment and run the app:

```bash
export AZURE_VOICELIVE_ENDPOINT=https://<resource>.services.ai.azure.com/
export AZURE_VOICELIVE_MODEL=gpt-realtime-2.1
export AZURE_VOICELIVE_VOICE=coral
uvicorn app.main:app --app-dir src/web --host 0.0.0.0 --port 8080
```

Open <http://localhost:8080/app>. The `/app` route is directly accessible when
`ALLOWED_TENANT_IDS` is empty. A deployed app can enable Container Apps Easy
Auth and set the tenant allowlist.

## Deployment

`infra/main.bicep` provisions the Container App, registry, managed identity, and
logging resources. Before deployment, replace the placeholder values in
`infra/main.bicepparam`. Build `src/web/Dockerfile`, update the Container App to
that image, and grant its managed identity the least-privilege role required to
invoke Voice Live on the configured Azure AI resource.

No API keys or access tokens belong in repository files. Local and deployed
sessions use `DefaultAzureCredential`.
