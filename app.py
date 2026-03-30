import os
from functools import wraps
from flask import (Flask, render_template, request, redirect,
                   url_for, session, flash, jsonify)
from pymongo import MongoClient
from bson.objectid import ObjectId
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "fallback_dev_key_change_this")

# ─── MongoDB ────────────────────────────────────────────────────────────────
uri = os.getenv("MONGO_URI")
client = MongoClient(uri)
db = client["garage_db"]
parts_col   = db["inventory"]
users_col   = db["users"]
orders_col  = db["orders"]
reviews_col = db["reviews"]

# ─── สร้าง Admin อัตโนมัติครั้งแรก ─────────────────────────────────────────
def create_initial_admin():
    if users_col.count_documents({}) == 0:
        admin_user = os.getenv("ADMIN_USERNAME", "admin")
        admin_pass = os.getenv("ADMIN_PASSWORD", "ChangeMe123!")
        users_col.insert_one({
            "username": admin_user,
            "password": generate_password_hash(admin_pass),
            "role": "admin",
            "email": "",
            "phone": "",
            "full_name": "ผู้ดูแลระบบ",
        })
create_initial_admin()

# ─── Decorators ──────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            flash("กรุณาเข้าสู่ระบบก่อน", "warning")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get("role") != "admin":
            flash("คุณไม่มีสิทธิ์เข้าถึงหน้านี้", "danger")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

# ─── หน้าหลัก ────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    search  = request.args.get("q", "").strip()
    cat     = request.args.get("category", "")
    brand   = request.args.get("brand", "")

    query = {}
    if search:
        query["name"] = {"$regex": search, "$options": "i"}
    if cat:
        query["category"] = cat
    if brand:
        query["compatible_brands"] = {"$in": [brand]}

    all_parts = list(parts_col.find(query))
    categories = parts_col.distinct("category")
    brands     = ["Honda", "Yamaha", "Suzuki", "Kawasaki", "TVS", "Ducati"]
    return render_template("index.html",
                           parts=all_parts,
                           categories=categories,
                           brands=brands,
                           search=search,
                           selected_cat=cat,
                           selected_brand=brand)

# ─── Auth ────────────────────────────────────────────────────────────────────
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username  = request.form.get("username", "").strip()
        email     = request.form.get("email", "").strip()
        password  = request.form.get("password", "")
        confirm   = request.form.get("confirm_password", "")

        if password != confirm:
            flash("รหัสผ่านไม่ตรงกัน", "danger")
            return redirect(url_for("register"))
        if users_col.find_one({"username": username}):
            flash("ชื่อผู้ใช้นี้ถูกใช้แล้ว", "danger")
            return redirect(url_for("register"))

        users_col.insert_one({
            "username":  username,
            "email":     email,
            "phone":     request.form.get("phone", "").strip(),
            "full_name": request.form.get("full_name", "").strip(),
            "password":  generate_password_hash(password),
            "role":      "customer",
            "address":   [],
        })
        flash("สมัครสมาชิกสำเร็จ!", "success")
        return redirect(url_for("login"))
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = users_col.find_one({"username": request.form.get("username")})
        if user and check_password_hash(user["password"], request.form.get("password")):
            session["user_id"]  = str(user["_id"])
            session["role"]     = user["role"]
            session["username"] = user["username"]
            session["cart"]     = session.get("cart", [])
            return redirect(url_for("admin_panel") if user["role"] == "admin" else url_for("index"))
        flash("ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง", "danger")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

# ─── ตะกร้าสินค้า (Invisible Personalization: แก้ไขปัญหา Session เต็ม) ───────────────────
@app.route("/cart/add/<id>", methods=["POST"])
@login_required
def add_to_cart(id):
    qty = int(request.form.get("qty", 1))
    part = parts_col.find_one({"_id": ObjectId(id)})
    if not part or part.get("quantity", 0) < qty:
        flash("สินค้าไม่เพียงพอ", "danger")
        return redirect(url_for("index"))

    cart = session.get("cart", [])
    for item in cart:
        if item["id"] == id:
            item["qty"] += qty
            break
    else:
        # ไม่เก็บรูปภาพใน session เพื่อลดขนาดคุกกี้
        cart.append({
            "id":    id,
            "name":  part["name"],
            "price": part["price"],
            "qty":   qty,
        })
    session["cart"] = cart
    session.modified = True
    flash(f"เพิ่ม {part['name']} ลงตะกร้าแล้ว", "success")
    return redirect(url_for("index"))

@app.route("/cart")
@login_required
def cart():
    cart_items = session.get("cart", [])
    display_cart = []
    total = 0

    for item in cart_items:
        # ดึงรูปจากฐานข้อมูลมาประกบตอนแสดงผลแทน
        part = parts_col.find_one({"_id": ObjectId(item["id"])})
        if part:
            item_data = {
                "id": item["id"],
                "name": item["name"],
                "price": item["price"],
                "qty": item["qty"],
                "image": part.get("image", ""),
                "subtotal": item["price"] * item["qty"]
            }
            display_cart.append(item_data)
            total += item_data["subtotal"]

    return render_template("cart.html", cart=display_cart, total=total)

@app.route("/cart/remove/<id>")
@login_required
def remove_from_cart(id):
    session["cart"] = [i for i in session.get("cart", []) if i["id"] != id]
    session.modified = True
    return redirect(url_for("cart"))

# ─── Checkout & Payment (แก้ไขให้ QR และที่แนบสลิปกลับมา) ─────────────────────
@app.route("/checkout", methods=["GET", "POST"])
@login_required
def checkout():
    cart_items = session.get("cart", [])
    if not cart_items:
        flash("ตะกร้าว่างเปล่า", "warning")
        return redirect(url_for("index"))
    
    total = sum(i["price"] * i["qty"] for i in cart_items)

    if request.method == "POST":
        import datetime, random, string
        order_id = "GP" + "".join(random.choices(string.digits, k=8))
        order = {
            "order_id": order_id,
            "user_id": session["user_id"],
            "username": session["username"],
            "items": cart_items,
            "total": total,
            "order_status": "รอชำระเงิน", # ต้องตรงกับใน HTML
            "payment_method": "promptpay",
            "created_at": datetime.datetime.now(),
            "address": {
                "full_name": request.form.get("full_name"),
                "phone": request.form.get("phone"),
                "address": request.form.get("address"),
                "province": request.form.get("province"),
                "zip_code": request.form.get("zip_code")
            }
        }
        res = orders_col.insert_one(order)
        # ลดสต็อก
        for i in cart_items:
            parts_col.update_one({"_id": ObjectId(i["id"])}, {"$inc": {"quantity": -i["qty"], "sold": i["qty"]}})
        
        session["cart"] = []
        session.modified = True
        return redirect(url_for("order_payment", order_id=str(res.inserted_id)))

    user = users_col.find_one({"_id": ObjectId(session["user_id"])})
    return render_template("checkout.html", cart=cart_items, total=total, user=user)

@app.route("/order/payment/<order_id>")
@login_required
def order_payment(order_id):
    order = orders_col.find_one({"_id": ObjectId(order_id)})
    # ส่ง promptpay_number ไปให้หน้า HTML ใช้เจน QR Code
    return render_template("payment.html", order=order, promptpay_number="0957520342")

@app.route("/order/upload_slip/<order_id>", methods=["POST"])
@login_required
def upload_slip(order_id):
    import base64
    file = request.files.get("slip")
    if file:
        data = base64.b64encode(file.read()).decode("utf-8")
        orders_col.update_one(
            {"_id": ObjectId(order_id)},
            {"$set": {"slip_image": f"data:{file.content_type};base64,{data}", "order_status": "รอตรวจสอบ"}}
        )
        flash("อัพโหลดสลิปสำเร็จ กำลังรอร้านค้าตรวจสอบ", "success")
    return redirect(url_for("my_orders"))

@app.route("/my-orders")
@login_required
def my_orders():
    orders = list(orders_col.find({"user_id": session["user_id"]}).sort("created_at", -1))
    return render_template("my_orders.html", orders=orders)

# ─── Admin Panel ──────────────────────────────────────────────────────────────
@app.route("/admin")
@admin_required
def admin_panel():
    all_p = list(parts_col.find())
    all_o = list(orders_col.find().sort("created_at", -1))
    return render_template("admin.html", 
                           parts=all_p, 
                           orders=all_o, 
                           total_revenue=sum(i.get("sold", 0)*i.get("price", 0) for i in all_p),
                           pending_orders=[o for o in all_o if o.get("order_status") == "รอตรวจสอบ"],
                           low_stock=[p for p in all_p if p.get("quantity", 0) <= 3])
@app.route("/admin/order/<order_id>/status", methods=["POST"])
@admin_required
def update_order_status(order_id):
    new_status = request.form.get("status")
    orders_col.update_one(
        {"_id": ObjectId(order_id)},
        {"$set": {"order_status": new_status}}
    )
    flash(f"อัปเดตสถานะออเดอร์เรียบร้อยเป็น: {new_status}", "success")
    return redirect(url_for("admin_panel"))

@app.route("/add", methods=["POST"])
@admin_required
def add_part():
    import base64
    image_data = ""
    file = request.files.get("image")
    if file and file.filename:
        data = base64.b64encode(file.read()).decode("utf-8")
        image_data = f"data:{file.content_type};base64,{data}"
    
    parts_col.insert_one({
        "name": request.form.get("name"),
        "price": float(request.form.get("price") or 0),
        "quantity": int(request.form.get("qty") or 0),
        "category": request.form.get("category"),
        "image": image_data,
        "sold": 0
    })
    flash("เพิ่มสินค้าสำเร็จ", "success")
    return redirect(url_for("admin_panel"))

@app.route("/edit/<id>", methods=["POST"])
@admin_required
def edit_part(id):
    import base64
    update_data = {
        "name": request.form.get("name"),
        "price": float(request.form.get("price") or 0),
        "quantity": int(request.form.get("qty") or 0),
        "category": request.form.get("category")
    }
    file = request.files.get("image")
    if file and file.filename:
        data = base64.b64encode(file.read()).decode("utf-8")
        update_data["image"] = f"data:{file.content_type};base64,{data}"

    parts_col.update_one({"_id": ObjectId(id)}, {"$set": update_data})
    flash("แก้ไขสินค้าสำเร็จ", "success")
    return redirect(url_for("admin_panel"))

@app.route("/delete/<id>")
@admin_required
def delete_part(id):
    parts_col.delete_one({"_id": ObjectId(id)})
    flash("ลบสำเร็จ", "success")
    return redirect(url_for("admin_panel"))

@app.route("/api/cart/count")
def cart_count():
    return jsonify({"count": sum(i["qty"] for i in session.get("cart", []))})

if __name__ == "__main__":
    app.run(debug=True)
