Deployment instructions

Local (Python)

1. Create a virtual environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Create a secrets file at `.streamlit/secrets.toml` (copy the example and fill values).

3. Run the app:

```bash
streamlit run streamlit_app.py
```

Local (Docker)

1. Build the image:

```bash
docker build -t iia-streamlit:latest .
```

2. Run the container:

```bash
docker run -p 8501:8501 --env-file .env -v "$PWD":/app iia-streamlit:latest
```

Alternatively, use `docker-compose up --build`.

Deploy to Streamlit Cloud

1. Push this repository to GitHub.
2. Create a new app on Streamlit Cloud and link the GitHub repo and branch.
3. In the Streamlit app settings, add the following secrets (Settings → Secrets):
   - `SUPABASE_URL`
   - `SUPABASE_KEY`
   - `APP_PASSWORD`
4. Deploy. Streamlit Cloud will install dependencies from `requirements.txt` and run the app.

Notes & Troubleshooting

- Ensure `.streamlit/secrets.toml` or Streamlit Cloud secrets are populated; missing keys will cause runtime errors in `backend.py`.
- If you prefer a container registry (Docker Hub, GitHub Container Registry), tag and push the image and deploy to your cloud provider.
