from flask import Flask, render_template, request, redirect, url_for, flash, make_response
from flask_mysqldb import MySQL
from flask_bcrypt import Bcrypt
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
import datetime
import csv
import io

app = Flask(__name__)
app.secret_key = "your_secret_key"  # Change this in production!

# MySQL config
app.config['MYSQL_HOST'] = 'localhost'
app.config['MYSQL_USER'] = 'tracker_user'
app.config['MYSQL_PASSWORD'] = 'password123'
app.config['MYSQL_DB'] = 'expense_tracker'

mysql = MySQL(app)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# User class for Flask-Login
class User(UserMixin):
    def __init__(self, id, name, email, password):
        self.id = id
        self.name = name
        self.email = email
        self.password = password

@login_manager.user_loader
def load_user(user_id):
    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM users WHERE id=%s", (user_id,))
    user = cur.fetchone()
    cur.close()
    if user:
        return User(id=user[0], name=user[1], email=user[2], password=user[3])
    return None

# Routes
@app.route("/")
def home():
    return render_template("index.html")

# Register
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["name"]
        email = request.form["email"]
        password = bcrypt.generate_password_hash(request.form["password"]).decode('utf-8')

        cur = mysql.connection.cursor()
        # Check if email exists
        cur.execute("SELECT * FROM users WHERE email=%s", (email,))
        existing_user = cur.fetchone()

        if existing_user:
            flash("This email is already registered! Please log in or use another.", "danger")
            cur.close()
            return redirect(url_for("register"))

        # Insert new user
        cur.execute("INSERT INTO users (name, email, password) VALUES (%s, %s, %s)", (name, email, password))
        mysql.connection.commit()
        cur.close()

        flash("Registration successful! Please login.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")

from flask import jsonify

# AJAX route to check email availability
@app.route("/check-email")
def check_email():
    email = request.args.get("email")
    cur = mysql.connection.cursor()
    cur.execute("SELECT id FROM users WHERE email=%s", (email,))
    user = cur.fetchone()
    cur.close()
    return jsonify({"exists": bool(user)})


# Login
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]

        cur = mysql.connection.cursor()
        cur.execute("SELECT * FROM users WHERE email=%s", (email,))
        user = cur.fetchone()
        cur.close()

        if user and bcrypt.check_password_hash(user[3], password):
            user_obj = User(id=user[0], name=user[1], email=user[2], password=user[3])
            login_user(user_obj)
            return redirect(url_for("dashboard"))
        else:
            flash("Invalid email or password.", "danger")

    return render_template("login.html")

# Dashboard
@app.route("/dashboard")
@login_required
def dashboard():
    cur = mysql.connection.cursor()

    # Fetch expenses
    cur.execute("SELECT amount, category, note, date FROM expenses WHERE user_id=%s", (current_user.id,))
    expenses = cur.fetchall()

    # Expenses summary by category
    cur.execute("SELECT category, SUM(amount) FROM expenses WHERE user_id=%s GROUP BY category", (current_user.id,))
    expense_summary = cur.fetchall()
    spent_dict = {cat: float(total) for cat, total in expense_summary}

    # Budgets
    cur.execute("SELECT category, amount FROM budgets WHERE user_id=%s", (current_user.id,))
    budgets = cur.fetchall()
    cur.close()

    labels = [cat for cat, _ in expense_summary]
    values = [float(total) for _, total in expense_summary]

    return render_template(
        "dashboard.html",
        name=current_user.name,
        expenses=expenses,
        labels=labels,
        values=values,
        budgets=budgets,
        spent_dict=spent_dict
    )

# Add expense
@app.route("/add-expense", methods=["GET", "POST"])
@login_required
def add_expense():
    if request.method == "POST":
        amount = request.form.get("amount")
        category = request.form.get("category")
        note = request.form.get("note")
        date = request.form.get("date")

        cur = mysql.connection.cursor()
        cur.execute(
            "INSERT INTO expenses (user_id, amount, category, note, date) VALUES (%s, %s, %s, %s, %s)",
            (current_user.id, amount, category, note, date)
        )
        mysql.connection.commit()
        cur.close()

        flash("Expense added successfully!", "success")
        return redirect(url_for("dashboard"))

    return render_template("add_expense.html")

# Set or update budget
@app.route("/set-budget", methods=["GET", "POST"])
@login_required
def set_budget():
    if request.method == "POST":
        category = request.form["category"]
        amount = request.form["amount"]

        cur = mysql.connection.cursor()
        cur.execute("SELECT id FROM budgets WHERE user_id=%s AND category=%s", (current_user.id, category))
        existing = cur.fetchone()

        if existing:
            cur.execute("UPDATE budgets SET amount=%s WHERE id=%s", (amount, existing[0]))
        else:
            cur.execute("INSERT INTO budgets (user_id, category, amount) VALUES (%s, %s, %s)", (current_user.id, category, amount))

        mysql.connection.commit()
        cur.close()
        return redirect(url_for("dashboard"))

    return render_template("set_budget.html")

# Reports
@app.route("/reports", methods=["GET", "POST"])
@login_required
def reports():
    cur = mysql.connection.cursor()
    now = datetime.datetime.now()
    selected_month = request.form.get("month", now.strftime("%Y-%m"))
    year, month = selected_month.split("-")

    cur.execute("""
        SELECT category, SUM(amount)
        FROM expenses
        WHERE user_id=%s AND YEAR(date)=%s AND MONTH(date)=%s
        GROUP BY category
    """, (current_user.id, year, month))
    monthly_summary = cur.fetchall()
    cur.close()

    labels = [cat for cat, _ in monthly_summary]
    values = [float(total) for _, total in monthly_summary]

    return render_template(
        "reports.html",
        selected_month=selected_month,
        labels=labels,
        values=values
    )

# Export CSV report
@app.route("/export/<string:month>")
@login_required
def export_report(month):
    year, month_num = month.split("-")

    cur = mysql.connection.cursor()
    cur.execute("""
        SELECT date, category, amount, note
        FROM expenses
        WHERE user_id=%s AND YEAR(date)=%s AND MONTH(date)=%s
        ORDER BY date ASC
    """, (current_user.id, year, month_num))
    rows = cur.fetchall()
    cur.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Date", "Category", "Amount", "Note"])
    writer.writerows(rows)

    response = make_response(output.getvalue())
    response.headers["Content-Disposition"] = f"attachment; filename=report_{month}.csv"
    response.headers["Content-Type"] = "text/csv"
    return response

# Logout
@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

if __name__ == "__main__":
    app.run(debug=True)
