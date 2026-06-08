# Cash Sack Budget

A Streamlit budgeting app built around daily check-ins, spending-category sacks, and earned coupon rewards.

Each user can create their own account and password. Budget data is separated by user inside the same SQLite database.

## What It Tracks

- Daily bank account balance
- Daily cash on hand
- Previous-day expenses
- Monthly category targets
- Earned coupons for previous-month goals

## Pages

- **Daily Entry**: enter balances, cash, notes, and expenses
- **Sacks of Cash**: view category sacks and add new budget targets
- **Coupons**: see earned demo coupons with fake QR-style codes

## Data Storage

The app stores data in:

```text
budget.db
```

That database file is created automatically in this project folder.

If an older single-user `budget.db` already exists, the app upgrades it for multiple users and assigns the old data to:

```text
username: imported
password: change-me-now
```

After signing in, use **Change password** in the sidebar.

## Run It

Install the requirements:

```powershell
pip install -r requirements.txt
```

Start the app:

```powershell
streamlit run streamlit_app.py
```

You can also run:

```powershell
python app.py
```

On Windows, you can also double-click:

```text
start.bat
```
