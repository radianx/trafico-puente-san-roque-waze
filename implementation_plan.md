# Implementation Plan: Evolution of PuenteHoy.com

This document serves as the implementation plan and technical roadmap for transitioning the border traffic tracker into a monetized SaaS platform under the new domain **PuenteHoy.com**.

---

## 🎯 Project Overview & Vision

- **Objective:** Evolve the current Posadas ↔ Encarnación crossing time page into a premium service.
- **Timeline:** Migration to the official domain **PuenteHoy.com** is scheduled for next week.
- **Monetization Model:**
  - **Year 1 (Freemium):** Grow active users organically and via digital ads. Offer standard real-time wait times for free, supported by donation links (Cafecito) and basic display ads. Collect historical data to build the dataset.
  - **Year 2 (Premium):** Introduce a micro-subscription tier ($2.50 USD/month) using **Mercado Pago** to unlock:
    - Interactive historical charts & typical wait-time heatmaps.
    - Free, cross-platform real-time **Web Push** notifications for configurable wait-time alerts (e.g., notify if wait time > 80 mins).

---

## ⚙️ Core Technical Decisions

1. **Backend Stack:** Keep Python + Flask. Incorporate **PostgreSQL** (hosted on a cloud provider like Supabase/Neon to keep Render hosting free/low) and utilize **Flask-SQLAlchemy** as the ORM.
2. **Payment Gateway:** **Mercado Pago** (dominant processor in the Argentina/Paraguay region).
3. **Alerts/Notifications Channel:** Browser **Web Push** notifications (integrated into the Progressive Web App setup, completely free, and natively supported on Android and iOS Standalone PWA).
4. **Data Retention:** Log every 5-minute scrape entry to the database and retain the logs indefinitely to feed rich analytics and ML prediction graphs.
5. **Authentication:** Standard Email and Password login (using bcrypt hashing) to minimize setup friction and cost.
6. **Data Visualization:** Interactive line charts showing 24h/7d wait-time history alongside an hour-of-day vs. day-of-week heatmap grid utilizing **Chart.js**.

---

## 🏗️ Technical Architecture Details

### 1. Database Schema (`models.py`)
- `User`:
  - `id` (Primary Key)
  - `email` (Unique, Indexed)
  - `password_hash` (Bcrypt)
  - `created_at` (Timestamp)
  - `is_premium` (Boolean, defaults to False)
  - `premium_expires_at` (Timestamp)
- `TrafficLog`:
  - `id` (Primary Key)
  - `direction` (String: 'ida' or 'vuelta')
  - `wait_time` (Integer, minutes)
  - `timestamp` (Timestamp, Indexed)
- `Subscription`:
  - `id` (Primary Key)
  - `user_id` (Foreign Key -> User)
  - `mp_preference_id` / `mp_subscription_id` (String)
  - `status` (String: active, pending, cancelled)
  - `amount` (Float)
  - `updated_at` (Timestamp)
- `PushSubscription`:
  - `id` (Primary Key)
  - `user_id` (Foreign Key -> User)
  - `endpoint` (Text)
  - `p256dh` (Text)
  - `auth` (Text)
  - `threshold_minutes` (Integer, default 80)

### 2. Backend Routes & Logic (`app.py`)
- **Scraper thread integration:** Update the background loop in `app.py` to commit to the database:
  ```python
  # In background thread loop
  db.session.add(TrafficLog(direction='ida', wait_time=tiempo_ida, timestamp=utc_now))
  db.session.add(TrafficLog(direction='vuelta', wait_time=tiempo_vuelta, timestamp=utc_now))
  db.session.commit()
  ```
- **Alert Dispatch Engine:** In the background thread, check if any user threshold is crossed:
  - Query all `PushSubscription` records where `threshold_minutes` <= current wait time.
  - Trigger web push payload using `pywebpush` to notify subscribers of the delay.
- **Authentication Handlers:**
  - `/api/auth/register` and `/api/auth/login` using session-based Flask cookies or JWT.
- **Payment Hooks:**
  - `/api/payments/mercadopago/webhook`: Listens to IPNs / Webhooks from Mercado Pago. Automatically updates `User.is_premium` and log subscriptions status.
- **Analytics API:**
  - `/api/trafico/historial`: Queries `TrafficLog`, group by hourly average over the requested window, and return JSON arrays for Chart.js.

### 3. Frontend & PWA Updates
- **Push Service Worker (`sw.js`):**
  - Implement a `push` listener:
    ```javascript
    self.addEventListener('push', function(event) {
      const data = event.data.json();
      self.registration.showNotification(data.title, {
        body: data.body,
        icon: '/static/images/icons/icon-192x192.png',
        badge: '/static/images/icons/badge-72x72.png'
      });
    });
    ```
- **Login Modals:** Build UI modal frames in `index.html` for login/registration forms.
- **Interactive Graphs:**
  - Instantiate `Chart.js` line graphs and grids when the user navigates to the Premium tab.
  - Block historical data views with a blur overlay and call-to-action to register/upgrade if `is_premium` is false.

---

## 🚀 Implementation Step-by-Step Checklist

- [ ] **Step 1: Setup Postgres & Database Models**
  - Create `models.py`.
  - Configure connection string parsing in `app.py`.
  - Install dependencies: `flask-sqlalchemy`, `psycopg2-binary`, `bcrypt`, `pywebpush`.
- [ ] **Step 2: Update Scraper Daemon**
  - Enable the scraper to commit each 5-minute scrape to `TrafficLog`.
- [ ] **Step 3: Build Authentication System**
  - Build login/register API endpoints.
  - Add session manager logic to track active user state.
- [ ] **Step 4: Integrate Mercado Pago Webhook**
  - Set up checkout preferences redirect/button on the frontend.
  - Create the webhook endpoint in `app.py` to handle subscription confirmations.
- [ ] **Step 5: Setup Web Push Notifications**
  - Generate VAPID key pairs.
  - Integrate PWA subscription request logic in the frontend JS.
  - Code push delivery payload triggers on the backend.
- [ ] **Step 6: Historical Visualizations**
  - Build the `/api/trafico/historial` endpoint.
  - Install and initialize Chart.js on the frontend.
  - Design the heatmap view.
- [ ] **Step 7: Launch & Domain Migration**
  - Point PuenteHoy.com to the Render application server.
  - Update environment variables and run staging smoke tests.
