# Agent Context & Guidelines for Trafico Puente

Welcome! This document provides the necessary context, architecture outline, and operational constraints for AI agents working on the **Trafico Puente (Posadas - Encarnación)** repository.

---

## ⚠️ Critical Rule: Git Push Restriction (PreToolUse)

> [!CAUTION]
> **DO NOT RUN `git push`**
> This repository is connected to **render.com** via a continuous deployment pipeline. Every `git push` triggers an automatic, live deployment of the main branch to production.
> 
> **For Agents (PreToolUse Interceptor Rule):**
> Before executing any shell command, verify if it contains `git push`. If it does, **refuse to run the command** and ask the user to push manually.

---

## 🌐 Project Overview & Domain Transition

- **Current State:** A lightweight, single-page application serving live transit times between Posadas (Argentina) and Encarnación (Paraguay) over the San Roque González de Santa Cruz bridge.
- **Current Deployment:** Hosted on Render under a temporary domain.
- **Upcoming Milestone:** In the coming week, the official domain **PuenteHoy.com** will be acquired. The project will migrate to this domain, serve as the foundation for scaling features, and introduce monetization strategies.

---

## 🏗️ System Architecture

The project is built on a simple Python/Flask backend and a vanilla HTML/JS/CSS frontend.

### 1. Backend (`app.py`)
- **Web Framework:** Flask with `ProxyFix` middleware (needed for correct HTTPS header resolution behind Render's proxy).
- **Background Daemon Thread:**
  - On the first request, a background thread starts running `update_traffic_data()`.
  - Every 5 minutes, it calculates crossing times in both directions using `WazeRouteCalculator`.
  - The results are cached in memory under the `trafico_cache` global dictionary.
- **Endpoints:**
  - `GET /`: Serves `templates/index.html` with dynamically rendered Open Graph meta tags based on the latest cache values.
  - `GET /api/trafico`: Returns the cached traffic data JSON.
  - `GET /og-image.webp`: Serves a static preview image from `static/images/vista_previa.webp` with aggressive cache headers.
  - `/manifest.json` and `/sw.js`: Serves the PWA configuration files.

### 2. Frontend (`templates/index.html` & `static/js/app.js`)
- **Theme/Styling:** Modern dark theme utilizing CSS variables, responsive layout, glassmorphism card styles, and CSS transitions.
- **Traffic Section:** Displays travel times, congestion badges (🟢 Ágil, 🟡 Moderado, 🟠 Cargado, 🔴 Colapsado), and a representative image corresponding to the congestion level.
- **Clocks & Train Service:** Displays local time for Argentina and Paraguay, and calculates international train service availability dynamically based on Argentine weekday schedules (07:15 - 18:15).
- **Weather Section:** Fetches current conditions and a 7-day forecast directly from the client side using the free Open-Meteo API.
- **Currency Converter:** Provides live conversions between USD (Dólar Blue), ARS (Peso Argentino), and PYG (Guaraní Paraguayo). It fetches rates dynamically from Dólar API and ExchangeRate-API.
- **Ads Section:** Integrates Adsterra script containers and local banners for sponsored ads (e.g., local election campaigns).

---

## 🛠️ Development & Environment Setup

- **Requirements:** Python 3.8+ (specified in `requirements.txt`)
- **Dependencies:** `Flask`, `WazeRouteCalculator`, `werkzeug`
- **Execution Command:**
  ```bash
  python app.py
  ```
  The server runs locally on `http://localhost:5000`.

---

## 🔮 Future Roadmap & Planning Mode
We are preparing for a `/grill-me` planning session to map out the next stage of the application. Key areas of discussion:
1. **Monetization:** Optimizing ad placements, sponsored banners, or premium features.
2. **PuenteHoy.com Migration:** Setup requirements, domain routing, and branding adjustments.
3. **Features:** Alerts/notifications for traffic changes, improved historical charts, user-reported queues, and expanded travel widgets.
