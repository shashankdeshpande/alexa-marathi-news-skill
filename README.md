# 🗞️ Alexa Marathi News Skill

![Python](https://img.shields.io/badge/Python-3.9+-blue.svg?style=flat&logo=python&logoColor=white)
![Alexa](https://img.shields.io/badge/Alexa-Skill-blue.svg?style=flat&logo=amazon-alexa&logoColor=white)
![Supabase](https://img.shields.io/badge/Supabase-Database%20%26%20Storage-green.svg?style=flat&logo=supabase&logoColor=white)

An Alexa Skill that plays the latest Marathi news headlines from [DD Sahyadri News](https://www.youtube.com/@DDSahyadriNews). It automates fetching audio from YouTube and streaming it via Alexa.

## 🗣️ Invocation

To listen to the news, just say:
> **"Alexa, play Marathi News"**

## 🏗️ Architecture

1.  Source: YouTube RSS Feed.
2.  Processing: Python script (`scripts/fetch_recent_news.py`) runs on GitHub Actions.
    *   Parses RSS feed.
    *   Checks PostgreSQL DB for duplicates.
    *   Fetches audio stream URL via RapidAPI and updates database.
    *   Updates this `README.md` with the latest news status.
3.  Backend: AWS Lambda handles Alexa requests.

## 🔄 Workflows

The project uses GitHub Actions to automate the news fetching process.

| Workflow | Schedule | Description |
| :--- | :--- | :--- |
| **Fetch Recent News** | Every 30 mins | Checks RSS feed, updates database with latest audio stream URL and refreshes this README. |

## 📰 Latest News

<!-- LATEST_NEWS_START -->
[![Watch on YouTube](https://img.youtube.com/vi/kykBmUF77N4/hqdefault.jpg)](https://www.youtube.com/watch?v=kykBmUF77N4)  
**[Headlines | DD Sahyadri News | सह्याद्री बातम्या | दुपारी ४.३० च्या हेडलाईन्स |](https://www.youtube.com/watch?v=kykBmUF77N4)**  
📅 09 Apr 2026 05:02 PM IST
<!-- LATEST_NEWS_END -->