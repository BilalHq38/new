# Pigeon flying Tournament Website

FastAPI + SQLite + Jinja2. Zero cost deployment on Render.

## Local Setup

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

Open http://localhost:8000

Admin panel: http://localhost:8000/admin/login
Default credentials: admin / pigeon123 (change in .env)

## Environment Variables (.env)

```
ADMIN_USERNAME=admin
ADMIN_PASSWORD=your-password-here
SECRET_KEY=any-random-string-here
```

## Deploy to Render (Free)

1. Push this folder to a GitHub repository
2. Go to https://render.com and create a free account
3. Click "New Web Service" and connect your GitHub repo
4. Set environment variables in Render dashboard:
   - ADMIN_USERNAME
   - ADMIN_PASSWORD
   - SECRET_KEY
5. Build command: pip install -r requirements.txt
6. Start command: uvicorn main:app --host 0.0.0.0 --port $PORT
7. Click Deploy

## Project Structure

```
main.py          - All routes and app logic
database.py      - SQLite setup, helpers
auth.py          - Admin session login
static/css/      - Stylesheet
static/js/       - Search, tabs, clock
static/uploads/  - Uploaded images
templates/       - All HTML pages
templates/admin/ - Admin panel pages
```

## Admin Workflow

1. Login at /admin/login
2. Create a tournament (name, template, start date, interval, banner)
3. Go to Participants and add all players
4. Go to Enter Times and fill in times as the tournament progresses
5. When all rounds are done, click Publish Results on the dashboard
6. SR rankings lock in and the public leaderboard shows final standings
