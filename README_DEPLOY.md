# Deploy Delvin Stock Screener Online

The simplest beginner-friendly path is Render. It can host this Python app and give you a public link like `https://delvin-stock-screener.onrender.com`.

## Files Needed

Upload these files to a GitHub repository:

- `stock_screener_app.py`
- `stock_screener_app_rebuilt.py`
- `requirements.txt`
- `render.yaml`

The app uses Python's built-in web server and free Yahoo Finance endpoints, so there are no required Python packages.

## Deploy On Render

1. Create a GitHub repository and upload the files above.
2. Go to Render and choose **New +** then **Web Service**.
3. Connect your GitHub repository.
4. Use these settings:
   - Runtime: `Python`
   - Build Command: `python --version`
   - Start Command: `python stock_screener_app_rebuilt.py`
   - Health Check Path: `/health`
5. Add this environment variable if Render does not pick it up from `render.yaml`:
   - `HOST=0.0.0.0`
6. Click **Deploy Web Service**.

After deployment, Render will show your public app URL. Share that URL with friends.

## Important Notes

- This app is useful for learning and screening, not official financial advice.
- Yahoo Finance data may occasionally be delayed, unavailable, or rate-limited.
- Free hosting can sleep when unused. The first visit after a quiet period may take a little longer to load.

## Local Testing

Run the app locally with:

```bash
python stock_screener_app_rebuilt.py
```

Then open:

```text
http://127.0.0.1:8890
```
