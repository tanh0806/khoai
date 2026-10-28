import json, os, time, unicodedata
from pathlib import Path
from datetime import date, datetime, timedelta
import mysql.connector
from mysql.connector import IntegrityError
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from google import genai
from google.genai import types

from rag import search_knowledge

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(dotenv_path=BASE_DIR / ".env", override=True)

DB_NAME = os.getenv("DB_NAME", "khoai")
DB_BASE = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", ""),
    "charset": "utf8mb4",
}
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
KEY = (os.getenv("GEMINI_API_KEY") or "").strip()
client = genai.Client(api_key=KEY) if KEY else None

app = Flask(__name__)
CORS(app)

ROLE_PAGES = {
    "Admin": ["Tổng quan","Hàng hóa","Danh mục","Kho","Nhà cung cấp","Nhập/Xuất","Chuyển kho","Lô hàng","Tồn kho","Quét mã","Cảnh báo","Báo cáo","AI","Tài khoản","Nhật ký","Hồ sơ"],
    "Thủ kho": ["Tổng quan","Hàng hóa","Danh mục","Kho","Nhập/Xuất","Chuyển kho","Lô hàng","Tồn kho","Quét mã","Cảnh báo","AI","Hồ sơ"],
    "Kế toán": ["Tổng quan","Hàng hóa","Danh mục","Kho","Nhà cung cấp","Nhập/Xuất","Báo cáo","AI","Hồ sơ"],
}

SYSTEM = """Bạn là KhoAI, trợ lý thông minh của hệ thống quản lý kho.

Bạn có thể nhận 3 nhóm ngữ cảnh:
1) MYSQL_DATA: số liệu hiện tại của hệ thống như hàng hóa, tồn kho, nhập/xuất, chuyển kho, lô, nhà cung cấp, báo cáo.
2) RAG_KNOWLEDGE: kiến thức nghiệp vụ, phân quyền, hướng dẫn sử dụng, C01-C09 và FAQ.
3) RECENT_CONVERSATION: một số lượt hội thoại gần nhất để hiểu câu hỏi nối tiếp.

Quy tắc bắt buộc:
- Không bịa mã hàng, số lượng, giá, kho, nhà cung cấp hoặc giao dịch.
- Số liệu hiện tại phải ưu tiên MYSQL_DATA; không lấy số tồn hiện tại từ RAG.
- Chỉ giao dịch trạng thái "Đã xác nhận" mới được coi là đã tác động tồn.
- Phiếu "Chờ xác nhận" chưa làm thay đổi tồn.
- Không xuất hoặc chuyển vượt tồn hiện tại.
- Thủ kho chỉ được phân tích kho được phân công.
- Kế toán chỉ xem/phân tích, không trực tiếp làm thay đổi tồn.
- AI chỉ đọc, tổng hợp, giải thích và khuyến nghị; không tự sửa MySQL hay tự xác nhận phiếu.
- Nếu thiếu dữ liệu, nói rõ chưa đủ dữ liệu.
- Trả lời tiếng Việt, ngắn gọn, rõ số liệu; ưu tiên dưới khoảng 8-12 dòng nếu câu hỏi đơn giản.
"""

def db(server=False):
    cfg = DB_BASE.copy()
    if not server:
        cfg["database"] = DB_NAME
    return mysql.connector.connect(**cfg)

def rows(sql, args=()):
    c = db(); q = c.cursor(dictionary=True)
    try:
        q.execute(sql, args)
        return q.fetchall()
    finally:
        q.close(); c.close()

def row(sql, args=()):
    x = rows(sql, args)
    return x[0] if x else None

def actor(user_id):
    if not user_id:
        return None
    return row("""SELECT u.id,u.username,u.email,u.name,u.role,u.warehouse_id,u.active,
                        w.code warehouse_code,w.name warehouse_name
                 FROM users u LEFT JOIN warehouses w ON w.id=u.warehouse_id
                 WHERE u.id=%s""", (user_id,))

def require_user(user_id, roles=None):
    u = actor(user_id)
    if not u or not u["active"]:
        return None, (jsonify(success=False, error="Phiên người dùng không hợp lệ."), 401)
    if roles and u["role"] not in roles:
        return None, (jsonify(success=False, error="Vai trò hiện tại không có quyền thực hiện thao tác này."), 403)
    return u, None

def log(cur, user_id, action, target, detail=""):
    cur.execute("INSERT INTO audit_logs(user_id,action,target,detail) VALUES(%s,%s,%s,%s)",
                (user_id, action, target, detail))

def existing_tables(cur):
    cur.execute("SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s", (DB_NAME,))
    return {r[0] for r in cur.fetchall()}

def table_columns(cur, table):
    cur.execute("SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
                (DB_NAME, table))
    return {r[0] for r in cur.fetchall()}

def ensure_column(cur, table, col, ddl):
    if table in existing_tables(cur) and col not in table_columns(cur, table):
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")

def migrate(cur):
    if "users" in existing_tables(cur):
        cols = table_columns(cur, "users")
        if "username" not in cols:
            cur.execute("ALTER TABLE users ADD COLUMN username VARCHAR(60) NULL AFTER id")
            cur.execute("UPDATE users SET username=CONCAT('user',id) WHERE username IS NULL")
            try: cur.execute("ALTER TABLE users ADD UNIQUE KEY uq_users_username(username)")
            except Exception: pass
    if "categories" in existing_tables(cur):
        cols = table_columns(cur, "categories")
        if "code" not in cols:
            cur.execute("ALTER TABLE categories ADD COLUMN code VARCHAR(30) NULL AFTER id")
            cur.execute("UPDATE categories SET code=CONCAT('DM',LPAD(id,3,'0')) WHERE code IS NULL")
            try: cur.execute("ALTER TABLE categories ADD UNIQUE KEY uq_categories_code(code)")
            except Exception: pass
    if "products" in existing_tables(cur):
        ensure_column(cur,"products","sale_price","sale_price DECIMAL(15,2) NOT NULL DEFAULT 0")
        ensure_column(cur,"products","barcode","barcode VARCHAR(80) NULL UNIQUE")
        ensure_column(cur,"products","description","description VARCHAR(500) NULL")
    if "warehouses" in existing_tables(cur):
        ensure_column(cur,"warehouses","status","status VARCHAR(30) NOT NULL DEFAULT 'Hoạt động'")
    if "suppliers" in existing_tables(cur):
        ensure_column(cur,"suppliers","status","status VARCHAR(30) NOT NULL DEFAULT 'Đang hợp tác'")
    if "audit_logs" in existing_tables(cur):
        ensure_column(cur,"audit_logs","target","target VARCHAR(150) NULL")

def init_db():
    c = db(True); q = c.cursor()
    q.execute(f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    c.commit(); q.close(); c.close()

    c = db(); q = c.cursor()
    ddl = [
        """CREATE TABLE IF NOT EXISTS warehouses(
            id INT AUTO_INCREMENT PRIMARY KEY, code VARCHAR(30) NOT NULL UNIQUE,
            name VARCHAR(150) NOT NULL, address VARCHAR(255),
            status VARCHAR(30) NOT NULL DEFAULT 'Hoạt động') ENGINE=InnoDB""",
        """CREATE TABLE IF NOT EXISTS users(
            id INT AUTO_INCREMENT PRIMARY KEY, username VARCHAR(60) NULL UNIQUE,
            email VARCHAR(150) NOT NULL UNIQUE, password VARCHAR(255) NOT NULL,
            name VARCHAR(120) NOT NULL, role ENUM('Admin','Thủ kho','Kế toán') NOT NULL,
            warehouse_id INT NULL, active TINYINT(1) NOT NULL DEFAULT 1,
            FOREIGN KEY(warehouse_id) REFERENCES warehouses(id) ON DELETE SET NULL) ENGINE=InnoDB""",
        """CREATE TABLE IF NOT EXISTS categories(
            id INT AUTO_INCREMENT PRIMARY KEY, code VARCHAR(30) NULL UNIQUE,
            name VARCHAR(120) NOT NULL UNIQUE, description VARCHAR(255)) ENGINE=InnoDB""",
        """CREATE TABLE IF NOT EXISTS suppliers(
            id INT AUTO_INCREMENT PRIMARY KEY, code VARCHAR(30) NOT NULL UNIQUE,
            name VARCHAR(150) NOT NULL, phone VARCHAR(30), email VARCHAR(150),
            address VARCHAR(255), status VARCHAR(30) NOT NULL DEFAULT 'Đang hợp tác') ENGINE=InnoDB""",
        """CREATE TABLE IF NOT EXISTS products(
            id INT AUTO_INCREMENT PRIMARY KEY, code VARCHAR(50) NOT NULL UNIQUE,
            name VARCHAR(180) NOT NULL, category_id INT NULL, unit VARCHAR(30) NOT NULL DEFAULT 'Cái',
            price DECIMAL(15,2) NOT NULL DEFAULT 0, sale_price DECIMAL(15,2) NOT NULL DEFAULT 0, min_stock INT NOT NULL DEFAULT 0,
            barcode VARCHAR(80) NULL UNIQUE, description VARCHAR(500) NULL,
            status VARCHAR(50) NOT NULL DEFAULT 'Đang sử dụng',
            CONSTRAINT ck_product_min CHECK(min_stock>=0),
            CONSTRAINT ck_product_price CHECK(price>=0), CONSTRAINT ck_product_sale_price CHECK(sale_price>=0),
            FOREIGN KEY(category_id) REFERENCES categories(id) ON DELETE SET NULL) ENGINE=InnoDB""",
        """CREATE TABLE IF NOT EXISTS inventory(
            warehouse_id INT NOT NULL, product_id INT NOT NULL, quantity INT NOT NULL DEFAULT 0,
            PRIMARY KEY(warehouse_id,product_id),
            CONSTRAINT ck_inventory_nonnegative CHECK(quantity>=0),
            FOREIGN KEY(warehouse_id) REFERENCES warehouses(id),
            FOREIGN KEY(product_id) REFERENCES products(id)) ENGINE=InnoDB""",
        """CREATE TABLE IF NOT EXISTS transactions(
            id INT AUTO_INCREMENT PRIMARY KEY, type ENUM('Nhập','Xuất') NOT NULL,
            warehouse_id INT NOT NULL, supplier_id INT NULL, product_id INT NOT NULL,
            quantity INT NOT NULL, price DECIMAL(15,2) NOT NULL DEFAULT 0,
            note VARCHAR(500), status ENUM('Chờ xác nhận','Đã xác nhận','Đã hủy') NOT NULL DEFAULT 'Chờ xác nhận',
            created_by INT NULL, confirmed_by INT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, confirmed_at DATETIME NULL,
            CONSTRAINT ck_tx_qty CHECK(quantity>0), CONSTRAINT ck_tx_price CHECK(price>=0),
            FOREIGN KEY(warehouse_id) REFERENCES warehouses(id),
            FOREIGN KEY(supplier_id) REFERENCES suppliers(id) ON DELETE SET NULL,
            FOREIGN KEY(product_id) REFERENCES products(id),
            FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE SET NULL,
            FOREIGN KEY(confirmed_by) REFERENCES users(id) ON DELETE SET NULL) ENGINE=InnoDB""",
        """CREATE TABLE IF NOT EXISTS transfers(
            id INT AUTO_INCREMENT PRIMARY KEY, from_warehouse_id INT NOT NULL,
            to_warehouse_id INT NOT NULL, product_id INT NOT NULL, quantity INT NOT NULL,
            note VARCHAR(500), status ENUM('Chờ xác nhận','Đã xác nhận','Đã hủy') NOT NULL DEFAULT 'Chờ xác nhận',
            created_by INT NULL, confirmed_by INT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, confirmed_at DATETIME NULL,
            CONSTRAINT ck_transfer_qty CHECK(quantity>0),
            FOREIGN KEY(from_warehouse_id) REFERENCES warehouses(id),
            FOREIGN KEY(to_warehouse_id) REFERENCES warehouses(id),
            FOREIGN KEY(product_id) REFERENCES products(id),
            FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE SET NULL,
            FOREIGN KEY(confirmed_by) REFERENCES users(id) ON DELETE SET NULL) ENGINE=InnoDB""",
        """CREATE TABLE IF NOT EXISTS lots(
            id INT AUTO_INCREMENT PRIMARY KEY, code VARCHAR(60) NOT NULL UNIQUE,
            warehouse_id INT NOT NULL, product_id INT NOT NULL, quantity INT NOT NULL DEFAULT 0,
            received_date DATE NULL, expiry_date DATE NULL,
            created_by INT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT ck_lot_qty CHECK(quantity>=0),
            FOREIGN KEY(warehouse_id) REFERENCES warehouses(id),
            FOREIGN KEY(product_id) REFERENCES products(id),
            FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE SET NULL) ENGINE=InnoDB""",
        """CREATE TABLE IF NOT EXISTS audit_logs(
            id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id INT NULL,
            action VARCHAR(150) NOT NULL, target VARCHAR(150), detail TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL) ENGINE=InnoDB""",
        """CREATE TABLE IF NOT EXISTS app_settings(
            setting_key VARCHAR(100) PRIMARY KEY,
            setting_value VARCHAR(500) NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB"""
    ]
    for s in ddl:
        q.execute(s)
    migrate(q)
    c.commit()

    # =========================================================
    # KHÔI PHỤC DỮ LIỆU HÀNG HÓA TỪ BẢN KHOAI CŨ
    # Chỉ thực hiện 1 lần để các lần khởi động sau không ghi đè
    # dữ liệu mà người dùng đã chỉnh sửa trong MySQL.
    # =========================================================
    q.execute("SELECT setting_value FROM app_settings WHERE setting_key='legacy_catalog_restored_v1'")
    legacy_done = q.fetchone()

    if not legacy_done:
        legacy_warehouses = [
            ("KHO01","Kho Laptop & Máy tính","Thái Nguyên","Hoạt động"),
            ("KHO02","Kho Linh kiện máy tính","Thái Nguyên","Hoạt động"),
            ("KHO03","Kho Phụ kiện điện tử","Thái Nguyên","Hoạt động"),
            ("KHO04","Kho Thiết bị mạng & Camera","Thái Nguyên","Hoạt động"),
        ]
        for code,name,address,status in legacy_warehouses:
            q.execute("""INSERT INTO warehouses(code,name,address,status)
                         VALUES(%s,%s,%s,%s)
                         ON DUPLICATE KEY UPDATE
                           name=VALUES(name),address=VALUES(address),status=VALUES(status)""",
                      (code,name,address,status))

        legacy_categories = [
            ("DM01","Laptop & Máy tính","Laptop và máy tính"),
            ("DM02","Linh kiện máy tính","RAM, SSD và linh kiện"),
            ("DM03","Phụ kiện điện tử","Màn hình, chuột, bàn phím"),
            ("DM04","Thiết bị mạng","Router, camera và thiết bị mạng"),
        ]
        for code,name,description in legacy_categories:
            q.execute("""INSERT INTO categories(code,name,description)
                         VALUES(%s,%s,%s)
                         ON DUPLICATE KEY UPDATE
                           name=VALUES(name),description=VALUES(description)""",
                      (code,name,description))

        legacy_suppliers = [
            ("NCC01","Acer Việt Nam","0901001001","acer@ncc.vn","Hà Nội","Đang hợp tác"),
            ("NCC02","Linh Kiện Tech","0901001002","tech@ncc.vn","Hà Nội","Đang hợp tác"),
            ("NCC03","Phụ Kiện Số","0901001003","pkso@ncc.vn","Bắc Ninh","Đang hợp tác"),
            ("NCC04","Network Pro","0901001004","network@ncc.vn","Hải Phòng","Đang hợp tác"),
        ]
        for code,name,phone,email,address,status in legacy_suppliers:
            q.execute("""INSERT INTO suppliers(code,name,phone,email,address,status)
                         VALUES(%s,%s,%s,%s,%s,%s)
                         ON DUPLICATE KEY UPDATE
                           name=VALUES(name),phone=VALUES(phone),email=VALUES(email),
                           address=VALUES(address),status=VALUES(status)""",
                      (code,name,phone,email,address,status))

        q.execute("SELECT id,code FROM categories")
        cats = {code:cid for cid,code in q.fetchall()}

        legacy_products = [
            ("SP001","Laptop Acer Nitro V15","DM01","Cái",18500000,20990000,5,"893850000001","Laptop gaming RTX 4050","Đang sử dụng"),
            ("SP002","RAM DDR5 16GB","DM02","Thanh",980000,1250000,15,"893850000002","RAM DDR5 16GB","Đang sử dụng"),
            ("SP003","SSD NVMe 1TB","DM02","Cái",1450000,1790000,12,"893850000003","SSD NVMe PCIe 4.0","Đang sử dụng"),
            ("SP004","Màn hình 24 inch 180Hz","DM03","Cái",3100000,3690000,8,"893850000004","Màn hình gaming 180Hz","Đang sử dụng"),
            ("SP005","Chuột Logitech M331","DM03","Cái",320000,419000,10,"893850000005","Chuột không dây","Đang sử dụng"),
            ("HH001","Bàn phím cơ RGB","DM03","Cái",620000,799000,10,"893850000006","Bàn phím cơ RGB","Đang sử dụng"),
            ("SP007","Router WiFi 6 AX3000","DM04","Cái",1250000,1590000,8,"893850000007","Router WiFi 6","Đang sử dụng"),
            ("SP008","Camera IP 2K","DM04","Cái",790000,990000,8,"893850000008","Camera IP 2K","Đang sử dụng"),
        ]

        # Bản MySQL rút gọn trước đây đã dùng barcode của SP001 cho HH001.
        # Tạm giải phóng barcode của các mã cũ để tránh xung đột UNIQUE khi khôi phục.
        old_codes = tuple(x[0] for x in legacy_products)
        placeholders = ",".join(["%s"] * len(old_codes))
        q.execute(f"UPDATE products SET barcode=NULL WHERE code IN ({placeholders})", old_codes)

        for code,name,cat_code,unit,price,sale_price,min_stock,barcode,description,status in legacy_products:
            q.execute("""INSERT INTO products(
                            code,name,category_id,unit,price,sale_price,min_stock,barcode,description,status
                         ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                         ON DUPLICATE KEY UPDATE
                            name=VALUES(name),category_id=VALUES(category_id),unit=VALUES(unit),
                            price=VALUES(price),sale_price=VALUES(sale_price),
                            min_stock=VALUES(min_stock),barcode=VALUES(barcode),
                            description=VALUES(description),status=VALUES(status)""",
                      (code,name,cats.get(cat_code),unit,price,sale_price,min_stock,barcode,description,status))

        # Nhập tồn kho cũ nếu cặp kho-hàng đó chưa tồn tại.
        # Không xóa tồn hiện tại để tránh làm mất các phiếu bạn vừa thử nghiệm.
        q.execute("SELECT id,code FROM warehouses")
        wh = {code:wid for wid,code in q.fetchall()}
        q.execute("SELECT id,code FROM products")
        ps = {code:pid for pid,code in q.fetchall()}
        legacy_stock = [
            ("KHO01","SP001",12),
            ("KHO02","SP002",38),
            ("KHO02","SP003",9),
            ("KHO03","SP004",6),
            ("KHO03","SP005",0),
            ("KHO03","HH001",5),
            ("KHO04","SP007",7),
            ("KHO04","SP008",22),
        ]
        for warehouse_code,product_code,qty in legacy_stock:
            wid,pid=wh.get(warehouse_code),ps.get(product_code)
            if wid and pid:
                q.execute("""INSERT IGNORE INTO inventory(warehouse_id,product_id,quantity)
                             VALUES(%s,%s,%s)""",(wid,pid,qty))

        q.execute("""INSERT INTO app_settings(setting_key,setting_value)
                     VALUES('legacy_catalog_restored_v1','Đã khôi phục 8 hàng hóa từ app.js cũ')
                     ON DUPLICATE KEY UPDATE setting_value=VALUES(setting_value)""")
        c.commit()

    # Luôn bảo đảm 3 tài khoản demo tồn tại.
    q.execute("SELECT id,code FROM warehouses")
    wh = {code:wid for wid,code in q.fetchall()}
    seeds=[
        ("admin","admin@khoai.vn","Admin","Admin",None),
        ("thukho","thukho@khoai.vn","Thủ kho","Thủ kho",wh.get("KHO01")),
        ("ketoan","ketoan@khoai.vn","Kế toán","Kế toán",None)
    ]
    for username,email,name,role,wid in seeds:
        q.execute("SELECT id FROM users WHERE email=%s",(email,))
        u=q.fetchone()
        if not u:
            q.execute("""INSERT INTO users(username,email,password,name,role,warehouse_id,active)
                         VALUES(%s,%s,%s,%s,%s,%s,1)""",
                      (username,email,generate_password_hash("123456"),name,role,wid))
        else:
            q.execute("""UPDATE users SET username=COALESCE(NULLIF(username,''),%s),
                         name=%s,role=%s,warehouse_id=%s,active=1 WHERE email=%s""",
                      (username,name,role,wid,email))
    c.commit(); q.close(); c.close()

def verify_password(stored, plain):
    stored=str(stored or "")
    if stored.startswith(("pbkdf2:","scrypt:")):
        try: return check_password_hash(stored,plain)
        except Exception: return False
    return stored==plain

def role_scope_sql(u, alias, column="warehouse_id"):
    if u["role"]=="Thủ kho":
        return f" AND {alias}.{column}=%s", (u["warehouse_id"],)
    return "", ()

def expiry_state(expiry):
    if not expiry: return "Không có hạn"
    d = expiry if isinstance(expiry,date) else datetime.strptime(str(expiry)[:10],"%Y-%m-%d").date()
    diff=(d-date.today()).days
    if diff<0:return "Đã hết hạn"
    if diff<=30:return "Sắp hết hạn"
    return "Còn hạn"

@app.get("/")
def home():
    return jsonify(status="ok",service="KhoAI")

@app.get("/api/health")
def health():
    try:
        x=row("SELECT DATABASE() db")
        return jsonify(success=True,mysql=True,database=x["db"],model=MODEL)
    except Exception as e:
        return jsonify(success=False,error="MySQL: "+str(e)),500

@app.post("/api/login")
def login():
    b=request.get_json(silent=True) or {}
    email=str(b.get("email","")).strip().lower(); pw=str(b.get("password","")); selected=str(b.get("role","")).strip()
    u=row("""SELECT u.id,u.username,u.email,u.password,u.name,u.role,u.warehouse_id,u.active,
                    w.code warehouse_code,w.name warehouse_name
             FROM users u LEFT JOIN warehouses w ON w.id=u.warehouse_id
             WHERE LOWER(u.email)=LOWER(%s)""",(email,))
    if not u or not u["active"] or not verify_password(u["password"],pw):
        return jsonify(success=False,error="Sai email/mật khẩu hoặc tài khoản đã khóa."),401
    if selected and selected!=u["role"]:
        return jsonify(success=False,error=f"Tài khoản này thuộc vai trò {u['role']}, không phải {selected}."),403
    if not str(u["password"]).startswith(("pbkdf2:","scrypt:")):
        c=db();q=c.cursor();q.execute("UPDATE users SET password=%s WHERE id=%s",(generate_password_hash(pw),u["id"]));c.commit();q.close();c.close()
    u.pop("password",None)
    return jsonify(success=True,user=u)

@app.post("/api/forgot-password")
def forgot():
    b=request.get_json(silent=True) or {}
    email=str(b.get("email","")).strip().lower(); pw=str(b.get("password",""))
    if len(pw)<6:return jsonify(success=False,error="Mật khẩu mới tối thiểu 6 ký tự."),400
    c=db();q=c.cursor()
    try:
        q.execute("SELECT id FROM users WHERE LOWER(email)=LOWER(%s) AND active=1",(email,));u=q.fetchone()
        if not u:return jsonify(success=False,error="Không tìm thấy email này."),404
        uid=u[0];q.execute("UPDATE users SET password=%s WHERE id=%s",(generate_password_hash(pw),uid))
        log(q,uid,"Đặt lại mật khẩu",f"users:{uid}","Đổi mật khẩu qua chức năng quên mật khẩu")
        c.commit();return jsonify(success=True)
    finally:
        q.close();c.close()

@app.get("/api/dashboard")
def dashboard():
    u,err=require_user(request.args.get("user_id"))
    if err:return err
    scope,args=role_scope_sql(u,"i")
    inv=rows("""SELECT i.quantity,p.min_stock,p.price FROM inventory i
                JOIN products p ON p.id=i.product_id WHERE 1=1"""+scope,args)
    product_count=row("SELECT COUNT(*) c FROM products")["c"]
    total_stock=sum(int(x["quantity"]) for x in inv)
    low_count=sum(1 for x in inv if int(x["quantity"])<=int(x["min_stock"]))
    inventory_value=sum(float(x["quantity"])*float(x["price"]) for x in inv)
    start=date.today()-timedelta(days=6)
    txscope,txargs=role_scope_sql(u,"t")
    tx=rows("""SELECT t.type,t.quantity,DATE(t.confirmed_at) d FROM transactions t
               WHERE t.status='Đã xác nhận' AND DATE(t.confirmed_at)>=%s"""+txscope,(start,*txargs))
    by={}
    for i in range(7):
        d=start+timedelta(days=i);by[str(d)]={"imp":0,"exp":0}
    for x in tx:
        k=str(x["d"])
        if k in by:
            if x["type"]=="Nhập":by[k]["imp"]+=int(x["quantity"])
            else:by[k]["exp"]+=int(x["quantity"])
    labels=["T2","T3","T4","T5","T6","T7","CN"]
    days=[]
    for i in range(7):
        d=start+timedelta(days=i);z=by[str(d)]
        days.append({"date":str(d),"label":labels[d.weekday()],"imp":z["imp"],"exp":z["exp"]})
    return jsonify(success=True,data={"product_count":product_count,"total_stock":total_stock,"low_count":low_count,"inventory_value":inventory_value,"days":days})

@app.get("/api/categories")
def categories():
    u,err=require_user(request.args.get("user_id"))
    if err:return err
    return jsonify(success=True,data=rows("SELECT id,code,name,description FROM categories ORDER BY code"))

@app.post("/api/categories")
def add_category():
    b=request.get_json(silent=True) or {};u,err=require_user(b.get("user_id"),{"Admin"})
    if err:return err
    c=db();q=c.cursor()
    try:
        q.execute("INSERT INTO categories(code,name,description) VALUES(%s,%s,%s)",(b.get("code"),b.get("name"),b.get("description")))
        cid=q.lastrowid;log(q,u["id"],"Thêm danh mục",f"categories:{cid}",f"{b.get('code')} - {b.get('name')}");c.commit()
        return jsonify(success=True,id=cid),201
    except IntegrityError:return jsonify(success=False,error="Mã hoặc tên danh mục đã tồn tại."),409
    finally:q.close();c.close()

@app.get("/api/warehouses")
def warehouses():
    u,err=require_user(request.args.get("user_id"))
    if err:return err
    return jsonify(success=True,data=rows("SELECT id,code,name,address,status FROM warehouses ORDER BY code"))

@app.post("/api/warehouses")
def add_warehouse():
    b=request.get_json(silent=True) or {};u,err=require_user(b.get("user_id"),{"Admin"})
    if err:return err
    c=db();q=c.cursor()
    try:
        q.execute("INSERT INTO warehouses(code,name,address,status) VALUES(%s,%s,%s,'Hoạt động')",(b.get("code"),b.get("name"),b.get("address")))
        wid=q.lastrowid;log(q,u["id"],"Thêm kho",f"warehouses:{wid}",f"{b.get('code')} - {b.get('name')}");c.commit()
        return jsonify(success=True,id=wid),201
    except IntegrityError:return jsonify(success=False,error="Mã kho đã tồn tại."),409
    finally:q.close();c.close()

@app.get("/api/suppliers")
def suppliers():
    u,err=require_user(request.args.get("user_id"),{"Admin","Kế toán"})
    if err:return err
    return jsonify(success=True,data=rows("SELECT id,code,name,phone,email,address,status FROM suppliers ORDER BY code"))

@app.post("/api/suppliers")
def add_supplier():
    b=request.get_json(silent=True) or {};u,err=require_user(b.get("user_id"),{"Admin"})
    if err:return err
    c=db();q=c.cursor()
    try:
        q.execute("""INSERT INTO suppliers(code,name,phone,email,address,status)
                     VALUES(%s,%s,%s,%s,%s,'Đang hợp tác')""",
                  (b.get("code"),b.get("name"),b.get("phone"),b.get("email"),b.get("address")))
        sid=q.lastrowid;log(q,u["id"],"Thêm nhà cung cấp",f"suppliers:{sid}",f"{b.get('code')} - {b.get('name')}");c.commit()
        return jsonify(success=True,id=sid),201
    except IntegrityError:return jsonify(success=False,error="Mã nhà cung cấp đã tồn tại."),409
    finally:q.close();c.close()

@app.get("/api/products")
def products():
    u,err=require_user(request.args.get("user_id"))
    if err:return err
    data=rows("""SELECT p.id,p.code,p.name,p.unit,p.price,p.sale_price,p.min_stock,p.barcode,p.description,p.status,
                       p.category_id,c.code category_code,c.name category
                FROM products p LEFT JOIN categories c ON c.id=p.category_id ORDER BY p.code""")
    return jsonify(success=True,data=data)

@app.post("/api/products")
def add_product():
    b=request.get_json(silent=True) or {};u,err=require_user(b.get("user_id"),{"Admin","Thủ kho"})
    if err:return err
    c=db();q=c.cursor()
    try:
        q.execute("""INSERT INTO products(code,name,category_id,unit,price,sale_price,min_stock,barcode,description,status)
                     VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'Đang sử dụng')""",
                  (b.get("code"),b.get("name"),b.get("category_id") or None,b.get("unit","Cái"),
                   b.get("price",0),b.get("sale_price",0),b.get("min_stock",0),b.get("barcode") or None,b.get("description")))
        pid=q.lastrowid;log(q,u["id"],"Thêm hàng hóa",f"products:{pid}",f"{b.get('code')} - {b.get('name')}");c.commit()
        return jsonify(success=True,id=pid),201
    except IntegrityError:return jsonify(success=False,error="C02/C04: Mã hàng hoặc mã vạch trùng, hoặc giá trị không hợp lệ."),400
    finally:q.close();c.close()

@app.get("/api/inventory")
def inventory():
    u,err=require_user(request.args.get("user_id"),{"Admin","Thủ kho"})
    if err:return err
    sql="""SELECT i.warehouse_id,w.code warehouse_code,w.name warehouse_name,i.product_id,
                  p.code product_code,p.name product_name,p.unit,i.quantity,p.min_stock,p.price
           FROM inventory i JOIN warehouses w ON w.id=i.warehouse_id
           JOIN products p ON p.id=i.product_id WHERE 1=1"""
    args=()
    if u["role"]=="Thủ kho":sql+=" AND i.warehouse_id=%s";args=(u["warehouse_id"],)
    return jsonify(success=True,data=rows(sql+" ORDER BY w.code,p.code",args))

@app.get("/api/transactions")
def transactions():
    u,err=require_user(request.args.get("user_id"))
    if err:return err
    sql="""SELECT t.id,t.type,t.warehouse_id,t.product_id,t.quantity,t.price,t.note,t.status,
                  t.created_at,t.confirmed_at,w.code warehouse_code,p.code product_code,p.name product_name,
                  s.name supplier_name,uc.name created_by_name,uf.name confirmed_by_name
           FROM transactions t JOIN warehouses w ON w.id=t.warehouse_id
           JOIN products p ON p.id=t.product_id
           LEFT JOIN suppliers s ON s.id=t.supplier_id
           LEFT JOIN users uc ON uc.id=t.created_by LEFT JOIN users uf ON uf.id=t.confirmed_by
           WHERE 1=1"""
    args=()
    if u["role"]=="Thủ kho":sql+=" AND t.warehouse_id=%s";args=(u["warehouse_id"],)
    return jsonify(success=True,data=rows(sql+" ORDER BY t.id DESC",args))

@app.post("/api/transactions")
def create_tx():
    b=request.get_json(silent=True) or {};u,err=require_user(b.get("user_id"),{"Admin","Thủ kho"})
    if err:return err
    qty=int(b.get("quantity",0) or 0)
    if qty<=0:return jsonify(success=False,error="C03: Số lượng phải > 0."),400
    wid=int(b.get("warehouse_id") or 0)
    if u["role"]=="Thủ kho" and int(u["warehouse_id"] or 0)!=wid:
        return jsonify(success=False,error="Thủ kho chỉ thao tác kho được phân công."),403
    if b.get("type") not in {"Nhập","Xuất"}:return jsonify(success=False,error="Loại phiếu không hợp lệ."),400
    c=db();q=c.cursor()
    try:
        q.execute("""INSERT INTO transactions(type,warehouse_id,supplier_id,product_id,quantity,price,note,status,created_by)
                     VALUES(%s,%s,%s,%s,%s,%s,%s,'Chờ xác nhận',%s)""",
                  (b.get("type"),wid,b.get("supplier_id"),b.get("product_id"),qty,b.get("price",0),b.get("note",""),u["id"]))
        tid=q.lastrowid;log(q,u["id"],"Tạo phiếu "+str(b.get("type")),f"transactions:{tid}",f"SL={qty}; chưa thay đổi tồn");c.commit()
        return jsonify(success=True,id=tid),201
    except Exception as e:c.rollback();return jsonify(success=False,error=str(e)),400
    finally:q.close();c.close()

@app.post("/api/transactions/<int:tid>/confirm")
def confirm_tx(tid):
    b = request.get_json(silent=True) or {}
    u, err = require_user(b.get("user_id"), {"Admin", "Thủ kho"})
    if err:
        return err

    c = db()
    q = c.cursor(dictionary=True)
    try:
        # C07: toàn bộ xác nhận chạy trong một transaction.
        c.start_transaction()

        q.execute("SELECT * FROM transactions WHERE id=%s FOR UPDATE", (tid,))
        t = q.fetchone()
        if not t:
            raise ValueError("Không tìm thấy phiếu.")
        if t["status"] != "Chờ xác nhận":
            raise ValueError("C06: Phiếu không còn ở trạng thái chờ xác nhận.")
        if u["role"] == "Thủ kho" and int(u["warehouse_id"] or 0) != int(t["warehouse_id"]):
            raise PermissionError("Không đúng kho được phân công.")

        q.execute(
            "SELECT quantity FROM inventory WHERE warehouse_id=%s AND product_id=%s FOR UPDATE",
            (t["warehouse_id"], t["product_id"]),
        )
        stock = q.fetchone()
        current = int(stock["quantity"]) if stock else 0
        qty = int(t["quantity"])

        if t["type"] == "Nhập":
            # Không dùng số âm trong INSERT; nhập luôn là số dương.
            q.execute(
                """INSERT INTO inventory(warehouse_id,product_id,quantity)
                   VALUES(%s,%s,%s)
                   ON DUPLICATE KEY UPDATE quantity=quantity+%s""",
                (t["warehouse_id"], t["product_id"], qty, qty),
            )
            new_quantity = current + qty

        elif t["type"] == "Xuất":
            # C08: kiểm tra trước khi UPDATE.
            if qty > current:
                raise ValueError(f"C08: Không đủ tồn kho. Hiện có {current}, yêu cầu xuất {qty}.")

            q.execute(
                """UPDATE inventory
                   SET quantity=quantity-%s
                   WHERE warehouse_id=%s AND product_id=%s""",
                (qty, t["warehouse_id"], t["product_id"]),
            )
            if q.rowcount == 0:
                raise ValueError("Không tìm thấy tồn kho của sản phẩm tại kho này.")
            new_quantity = current - qty
        else:
            raise ValueError("Loại phiếu không hợp lệ.")

        # C01: kiểm tra cuối trước commit.
        if new_quantity < 0:
            raise ValueError("C01: Tồn kho không được âm.")

        q.execute(
            """UPDATE transactions
               SET status='Đã xác nhận',confirmed_by=%s,confirmed_at=NOW()
               WHERE id=%s""",
            (u["id"], tid),
        )
        log(
            q,
            u["id"],
            "Xác nhận phiếu " + t["type"],
            f"transactions:{tid}",
            f"SL={qty}; tồn trước={current}; tồn sau={new_quantity}",
        )
        c.commit()
        return jsonify(
            success=True,
            message=f"Đã xác nhận phiếu {t['type']}. Tồn trước {current}, tồn sau {new_quantity}.",
        )

    except PermissionError as e:
        c.rollback()
        return jsonify(success=False, error=str(e)), 403
    except Exception as e:
        c.rollback()
        return jsonify(success=False, error=str(e)), 400
    finally:
        q.close()
        c.close()

@app.post("/api/transactions/<int:tid>/cancel")
def cancel_tx(tid):
    b=request.get_json(silent=True) or {};u,err=require_user(b.get("user_id"),{"Admin","Thủ kho"})
    if err:return err
    c=db();q=c.cursor(dictionary=True)
    try:
        c.start_transaction();q.execute("SELECT * FROM transactions WHERE id=%s FOR UPDATE",(tid,));t=q.fetchone()
        if not t or t["status"]!="Chờ xác nhận":raise ValueError("Chỉ hủy được phiếu chờ xác nhận.")
        if u["role"]=="Thủ kho" and int(u["warehouse_id"] or 0)!=int(t["warehouse_id"]):raise PermissionError("Không đúng kho được phân công.")
        q.execute("UPDATE transactions SET status='Đã hủy' WHERE id=%s",(tid,))
        log(q,u["id"],"Hủy phiếu",f"transactions:{tid}","Tồn kho không thay đổi");c.commit();return jsonify(success=True)
    except PermissionError as e:c.rollback();return jsonify(success=False,error=str(e)),403
    except Exception as e:c.rollback();return jsonify(success=False,error=str(e)),400
    finally:q.close();c.close()

@app.get("/api/transfers")
def get_transfers():
    u,err=require_user(request.args.get("user_id"),{"Admin","Thủ kho"})
    if err:return err
    sql="""SELECT t.*,wf.code from_code,wt.code to_code,p.code product_code,p.name product_name,
                  uc.name created_by_name,uf.name confirmed_by_name
           FROM transfers t JOIN warehouses wf ON wf.id=t.from_warehouse_id
           JOIN warehouses wt ON wt.id=t.to_warehouse_id
           JOIN products p ON p.id=t.product_id
           LEFT JOIN users uc ON uc.id=t.created_by LEFT JOIN users uf ON uf.id=t.confirmed_by
           WHERE 1=1"""
    args=()
    if u["role"]=="Thủ kho":sql+=" AND t.from_warehouse_id=%s";args=(u["warehouse_id"],)
    return jsonify(success=True,data=rows(sql+" ORDER BY t.id DESC",args))

@app.post("/api/transfers")
def create_transfer():
    b=request.get_json(silent=True) or {};u,err=require_user(b.get("user_id"),{"Admin","Thủ kho"})
    if err:return err
    fw=int(b.get("from_warehouse_id") or 0);tw=int(b.get("to_warehouse_id") or 0);qty=int(b.get("quantity",0) or 0)
    if fw==tw:return jsonify(success=False,error="Kho nguồn và kho đích phải khác nhau."),400
    if qty<=0:return jsonify(success=False,error="Số lượng chuyển phải > 0."),400
    if u["role"]=="Thủ kho" and int(u["warehouse_id"] or 0)!=fw:return jsonify(success=False,error="Thủ kho chỉ được chuyển hàng từ kho mình phụ trách."),403
    c=db();q=c.cursor()
    try:
        q.execute("""INSERT INTO transfers(from_warehouse_id,to_warehouse_id,product_id,quantity,note,status,created_by)
                     VALUES(%s,%s,%s,%s,%s,'Chờ xác nhận',%s)""",(fw,tw,b.get("product_id"),qty,b.get("note",""),u["id"]))
        tid=q.lastrowid;log(q,u["id"],"Tạo phiếu chuyển kho",f"transfers:{tid}",f"SL={qty}");c.commit()
        return jsonify(success=True,id=tid),201
    except Exception as e:c.rollback();return jsonify(success=False,error=str(e)),400
    finally:q.close();c.close()

@app.post("/api/transfers/<int:tid>/confirm")
def confirm_transfer(tid):
    b=request.get_json(silent=True) or {};u,err=require_user(b.get("user_id"),{"Admin","Thủ kho"})
    if err:return err
    c=db();q=c.cursor(dictionary=True)
    try:
        c.start_transaction();q.execute("SELECT * FROM transfers WHERE id=%s FOR UPDATE",(tid,));t=q.fetchone()
        if not t or t["status"]!="Chờ xác nhận":raise ValueError("Phiếu chuyển không còn ở trạng thái chờ xác nhận.")
        if u["role"]=="Thủ kho" and int(u["warehouse_id"] or 0)!=int(t["from_warehouse_id"]):raise PermissionError("Không đúng kho nguồn được phân công.")
        q.execute("SELECT quantity FROM inventory WHERE warehouse_id=%s AND product_id=%s FOR UPDATE",(t["from_warehouse_id"],t["product_id"]));s=q.fetchone();current=int(s["quantity"]) if s else 0
        if int(t["quantity"])>current:raise ValueError(f"Không đủ tồn để chuyển. Hiện có {current}, yêu cầu {t['quantity']}.")
        q.execute("UPDATE inventory SET quantity=quantity-%s WHERE warehouse_id=%s AND product_id=%s",(t["quantity"],t["from_warehouse_id"],t["product_id"]))
        q.execute("""INSERT INTO inventory(warehouse_id,product_id,quantity) VALUES(%s,%s,%s)
                     ON DUPLICATE KEY UPDATE quantity=quantity+VALUES(quantity)""",(t["to_warehouse_id"],t["product_id"],t["quantity"]))
        q.execute("UPDATE transfers SET status='Đã xác nhận',confirmed_by=%s,confirmed_at=NOW() WHERE id=%s",(u["id"],tid))
        log(q,u["id"],"Xác nhận chuyển kho",f"transfers:{tid}",f"SL={t['quantity']}");c.commit();return jsonify(success=True)
    except PermissionError as e:c.rollback();return jsonify(success=False,error=str(e)),403
    except Exception as e:c.rollback();return jsonify(success=False,error=str(e)),400
    finally:q.close();c.close()

@app.post("/api/transfers/<int:tid>/cancel")
def cancel_transfer(tid):
    b=request.get_json(silent=True) or {};u,err=require_user(b.get("user_id"),{"Admin","Thủ kho"})
    if err:return err
    c=db();q=c.cursor(dictionary=True)
    try:
        c.start_transaction();q.execute("SELECT * FROM transfers WHERE id=%s FOR UPDATE",(tid,));t=q.fetchone()
        if not t or t["status"]!="Chờ xác nhận":raise ValueError("Chỉ hủy được phiếu chuyển chờ xác nhận.")
        if u["role"]=="Thủ kho" and int(u["warehouse_id"] or 0)!=int(t["from_warehouse_id"]):raise PermissionError("Không đúng kho nguồn được phân công.")
        q.execute("UPDATE transfers SET status='Đã hủy' WHERE id=%s",(tid,))
        log(q,u["id"],"Hủy phiếu chuyển kho",f"transfers:{tid}","Tồn kho không thay đổi");c.commit();return jsonify(success=True)
    except PermissionError as e:c.rollback();return jsonify(success=False,error=str(e)),403
    except Exception as e:c.rollback();return jsonify(success=False,error=str(e)),400
    finally:q.close();c.close()

@app.get("/api/lots")
def get_lots():
    u,err=require_user(request.args.get("user_id"),{"Admin","Thủ kho"})
    if err:return err
    sql="""SELECT l.id,l.code,l.warehouse_id,l.product_id,l.quantity,l.received_date,l.expiry_date,l.created_at,
                  w.code warehouse_code,w.name warehouse_name,p.code product_code,p.name product_name
           FROM lots l JOIN warehouses w ON w.id=l.warehouse_id JOIN products p ON p.id=l.product_id WHERE 1=1"""
    args=()
    if u["role"]=="Thủ kho":sql+=" AND l.warehouse_id=%s";args=(u["warehouse_id"],)
    data=rows(sql+" ORDER BY l.id DESC",args)
    for x in data:x["expiry_state"]=expiry_state(x["expiry_date"])
    return jsonify(success=True,data=data)

@app.post("/api/lots")
def add_lot():
    b=request.get_json(silent=True) or {};u,err=require_user(b.get("user_id"),{"Admin","Thủ kho"})
    if err:return err
    wid=int(b.get("warehouse_id") or 0);qty=int(b.get("quantity",0) or 0)
    if qty<0:return jsonify(success=False,error="Số lượng theo lô không được âm."),400
    if u["role"]=="Thủ kho" and int(u["warehouse_id"] or 0)!=wid:return jsonify(success=False,error="Thủ kho chỉ thêm lô cho kho được phân công."),403
    c=db();q=c.cursor()
    try:
        q.execute("""INSERT INTO lots(code,warehouse_id,product_id,quantity,received_date,expiry_date,created_by)
                     VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                  (b.get("code"),wid,b.get("product_id"),qty,b.get("received_date") or None,b.get("expiry_date") or None,u["id"]))
        lid=q.lastrowid;log(q,u["id"],"Thêm lô hàng",f"lots:{lid}",f"Mã lô={b.get('code')}; SL={qty}");c.commit()
        return jsonify(success=True,id=lid),201
    except IntegrityError:return jsonify(success=False,error="Mã lô đã tồn tại hoặc dữ liệu không hợp lệ."),409
    finally:q.close();c.close()

@app.get("/api/scan")
def scan():
    u,err=require_user(request.args.get("user_id"),{"Admin","Thủ kho"})
    if err:return err
    qv=str(request.args.get("q","")).strip()
    if not qv:return jsonify(success=False,error="Nhập mã hoặc tên cần tra."),400
    p=row("""SELECT p.id,p.code,p.name,p.barcode,p.price,p.min_stock,p.unit,p.description,c.name category
             FROM products p LEFT JOIN categories c ON c.id=p.category_id
             WHERE p.code=%s OR p.barcode=%s OR p.name LIKE %s ORDER BY (p.code=%s) DESC LIMIT 1""",(qv,qv,f"%{qv}%",qv))
    if not p:return jsonify(success=True,data=None)
    sql="""SELECT i.quantity,w.code warehouse_code,w.name warehouse_name FROM inventory i
           JOIN warehouses w ON w.id=i.warehouse_id WHERE i.product_id=%s"""
    args=[p["id"]]
    if u["role"]=="Thủ kho":sql+=" AND i.warehouse_id=%s";args.append(u["warehouse_id"])
    p["stock"]=rows(sql,tuple(args))
    return jsonify(success=True,data=p)

def build_alerts(u):
    result=[]
    sql="""SELECT i.quantity,p.min_stock,p.code product_code,p.name product_name,w.code warehouse_code
           FROM inventory i JOIN products p ON p.id=i.product_id JOIN warehouses w ON w.id=i.warehouse_id WHERE 1=1"""
    args=()
    if u["role"]=="Thủ kho":sql+=" AND i.warehouse_id=%s";args=(u["warehouse_id"],)
    for x in rows(sql,args):
        q=int(x["quantity"]);mn=int(x["min_stock"])
        if q<=0:result.append({"level":"Nguy hiểm","type":"Tồn kho","subject":f"{x['product_code']} - {x['product_name']}","detail":f"{x['warehouse_code']}: đã hết hàng"})
        elif q<=mn:result.append({"level":"Cảnh báo","type":"Tồn kho","subject":f"{x['product_code']} - {x['product_name']}","detail":f"{x['warehouse_code']}: tồn {q}, mức tối thiểu {mn}"})
    lsql="""SELECT l.code,l.expiry_date,p.code product_code,p.name product_name,w.code warehouse_code
            FROM lots l JOIN products p ON p.id=l.product_id JOIN warehouses w ON w.id=l.warehouse_id
            WHERE l.expiry_date IS NOT NULL AND l.expiry_date<=DATE_ADD(CURDATE(),INTERVAL 30 DAY)"""
    largs=()
    if u["role"]=="Thủ kho":lsql+=" AND l.warehouse_id=%s";largs=(u["warehouse_id"],)
    for x in rows(lsql,largs):
        state=expiry_state(x["expiry_date"])
        result.append({"level":"Nguy hiểm" if state=="Đã hết hạn" else "Cảnh báo","type":"Lô hàng","subject":f"Lô {x['code']} - {x['product_code']}","detail":f"{x['warehouse_code']}: {state}, hạn {x['expiry_date']}"})
    return result

@app.get("/api/alerts")
def get_alerts():
    u,err=require_user(request.args.get("user_id"),{"Admin","Thủ kho"})
    if err:return err
    return jsonify(success=True,data=build_alerts(u))

def report_data(u):
    inv=rows("""SELECT i.quantity,p.price FROM inventory i JOIN products p ON p.id=i.product_id""")
    inventory_value=sum(float(x["quantity"])*float(x["price"]) for x in inv)
    tx=rows("""SELECT t.type,t.quantity,t.price,t.confirmed_at,s.name supplier_name
               FROM transactions t LEFT JOIN suppliers s ON s.id=t.supplier_id
               WHERE t.status='Đã xác nhận'""")
    import_value=sum(float(x["quantity"])*float(x["price"]) for x in tx if x["type"]=="Nhập")
    export_value=sum(float(x["quantity"])*float(x["price"]) for x in tx if x["type"]=="Xuất")
    month={}
    sup={}
    for x in tx:
        m=str(x["confirmed_at"])[:7] if x["confirmed_at"] else "Không rõ"
        month.setdefault(m,{"month":m,"import_value":0,"export_value":0})
        v=float(x["quantity"])*float(x["price"])
        if x["type"]=="Nhập":
            month[m]["import_value"]+=v
            sn=x["supplier_name"] or "Không xác định"
            sup.setdefault(sn,{"supplier_name":sn,"value":0,"receipts":0})
            sup[sn]["value"]+=v;sup[sn]["receipts"]+=1
        else:month[m]["export_value"]+=v
    return {"inventory_value":inventory_value,"import_value":import_value,"export_value":export_value,"confirmed_count":len(tx),
            "monthly":sorted(month.values(),key=lambda x:x["month"],reverse=True)[:12],
            "suppliers":sorted(sup.values(),key=lambda x:x["value"],reverse=True)[:10]}

@app.get("/api/reports")
def reports_api():
    u,err=require_user(request.args.get("user_id"),{"Admin","Kế toán"})
    if err:return err
    return jsonify(success=True,data=report_data(u))

@app.get("/api/accounts")
def accounts():
    admin,err=require_user(request.args.get("user_id"),{"Admin"})
    if err:return err
    data=rows("""SELECT u.id,u.username,u.email,u.name,u.role,u.active,u.warehouse_id,w.code warehouse_code
                 FROM users u LEFT JOIN warehouses w ON w.id=u.warehouse_id
                 WHERE u.role IN ('Thủ kho','Kế toán') ORDER BY u.role,u.name""")
    return jsonify(success=True,data=data)

@app.post("/api/accounts/<int:uid>/set-password")
def admin_set_password(uid):
    b=request.get_json(silent=True) or {};admin,err=require_user(b.get("admin_id"),{"Admin"})
    if err:return err
    password=str(b.get("password",""))
    if len(password)<6:return jsonify(success=False,error="Mật khẩu phải có ít nhất 6 ký tự."),400
    target=actor(uid)
    if not target:return jsonify(success=False,error="Không tìm thấy tài khoản."),404
    if target["role"] not in {"Thủ kho","Kế toán"}:return jsonify(success=False,error="Admin chỉ cấp mật khẩu cho Thủ kho hoặc Kế toán."),403
    c=db();q=c.cursor()
    try:
        q.execute("UPDATE users SET password=%s WHERE id=%s",(generate_password_hash(password),uid))
        log(q,admin["id"],"Admin cấp mật khẩu",f"users:{uid}",f"Cấp mật khẩu mới cho {target['name']} ({target['role']})")
        c.commit();return jsonify(success=True,message="Đã cấp mật khẩu mới.")
    except Exception as e:c.rollback();return jsonify(success=False,error=str(e)),500
    finally:q.close();c.close()

@app.get("/api/audit-logs")
def audit_logs():
    u,err=require_user(request.args.get("user_id"),{"Admin"})
    if err:return err
    return jsonify(success=True,data=rows("""SELECT a.created_at,u.name user_name,a.action,a.target,a.detail
                                            FROM audit_logs a LEFT JOIN users u ON u.id=a.user_id
                                            ORDER BY a.id DESC LIMIT 300"""))

def normalize(s):
    s = unicodedata.normalize("NFD", str(s or "").lower())
    return "".join(ch for ch in s if unicodedata.category(ch) != "Mn")


RULES = {
    "C01": "Tồn kho không âm.",
    "C02": "Tồn tối thiểu phải >= 0.",
    "C03": "Số lượng trong phiếu phải > 0.",
    "C04": "Các mã quan trọng phải UNIQUE.",
    "C05": "Các bảng liên quan dùng khóa ngoại để bảo đảm toàn vẹn dữ liệu.",
    "C06": "Chỉ phiếu Đã xác nhận mới làm thay đổi tồn kho.",
    "C07": "Xác nhận nhập/xuất dùng transaction, commit và rollback khi lỗi.",
    "C08": "Không được xuất hoặc chuyển số lượng lớn hơn tồn hiện tại.",
    "C09": "Hệ thống ghi nhật ký người dùng, thời gian, hành động, đối tượng và chi tiết.",
}


def _warehouse_filter(u, alias="i", column="warehouse_id"):
    if u["role"] == "Thủ kho":
        return f" AND {alias}.{column}=%s", (u["warehouse_id"],)
    return "", ()


def fast_local_answer(u, msg):
    """Trả lời các câu phổ biến trực tiếp từ MySQL/RULES, không gọi Gemini/RAG."""
    n = normalize(msg)

    if any(k in n for k in ["quyen cua toi", "toi duoc lam gi", "chuc nang cua toi", "vai tro cua toi"]):
        return f"Vai trò {u['role']} được dùng: " + ", ".join(ROLE_PAGES[u["role"]]) + "."

    if ("c01" in n and "c09" in n) or "c01 den c09" in n or "c01-den-c09" in n:
        return "\n".join(f"{k}: {v}" for k, v in RULES.items())
    for code, detail in RULES.items():
        if code.lower() in n:
            return f"{code}: {detail}"

    if any(k in n for k in ["sap het", "het hang", "can nhap them", "nen nhap", "de xuat mat hang"]):
        scope, args = _warehouse_filter(u, "i")
        data = rows(
            """SELECT w.code warehouse,p.code product_code,p.name product_name,
                      i.quantity,p.min_stock
               FROM inventory i
               JOIN products p ON p.id=i.product_id
               JOIN warehouses w ON w.id=i.warehouse_id
               WHERE i.quantity<=p.min_stock""" + scope +
            " ORDER BY (p.min_stock-i.quantity) DESC,p.code LIMIT 30",
            args,
        )
        if not data:
            return "Hiện không có mặt hàng nào ở mức tồn nhỏ hơn hoặc bằng tồn tối thiểu trong phạm vi bạn được xem."
        recommend = any(k in n for k in ["can nhap them", "nen nhap", "de xuat"])
        lines = []
        for x in data[:20]:
            if recommend:
                target = max(int(x["min_stock"]) * 2, int(x["min_stock"]) + 1)
                qty = max(target - int(x["quantity"]), 0)
                lines.append(
                    f"- {x['warehouse']} · {x['product_code']} {x['product_name']}: tồn {x['quantity']}, "
                    f"tối thiểu {x['min_stock']}, đề xuất nhập khoảng {qty}."
                )
            else:
                lines.append(
                    f"- {x['warehouse']} · {x['product_code']} {x['product_name']}: "
                    f"tồn {x['quantity']}, tối thiểu {x['min_stock']}."
                )
        return ("Các mặt hàng cần chú ý:\n" if not recommend else "Đề xuất bổ sung hàng từ dữ liệu hiện tại:\n") + "\n".join(lines)

    # Tổng tồn
    if any(k in n for k in ["tong ton", "ton kho hien tai", "tong so luong ton"]):
        scope, args = _warehouse_filter(u, "i")
        x = row("SELECT COALESCE(SUM(i.quantity),0) total FROM inventory i WHERE 1=1" + scope, args)
        return f"Tổng tồn trong phạm vi bạn được xem là {int(x['total'] or 0)} đơn vị."

    # Phiếu nhập/xuất đang chờ
    if "phieu" in n and "cho" in n and "chuyen" not in n:
        scope, args = _warehouse_filter(u, "t")
        data = rows(
            """SELECT t.id,t.type,w.code warehouse,p.code product_code,p.name product_name,t.quantity
               FROM transactions t
               JOIN warehouses w ON w.id=t.warehouse_id
               JOIN products p ON p.id=t.product_id
               WHERE t.status='Chờ xác nhận'""" + scope + " ORDER BY t.id DESC LIMIT 20",
            args,
        )
        if not data:
            return "Hiện không có phiếu Nhập/Xuất nào đang chờ xác nhận trong phạm vi bạn được xem."
        return "Phiếu đang chờ:\n" + "\n".join(
            f"- #{x['id']} {x['type']} · {x['warehouse']} · {x['product_code']} {x['product_name']} · SL {x['quantity']}"
            for x in data
        )

    # Chuyển kho đang chờ
    if "chuyen kho" in n and "cho" in n and u["role"] in {"Admin", "Thủ kho"}:
        sql = """SELECT t.id,wf.code from_warehouse,wt.code to_warehouse,p.code product_code,p.name product_name,t.quantity
                 FROM transfers t
                 JOIN warehouses wf ON wf.id=t.from_warehouse_id
                 JOIN warehouses wt ON wt.id=t.to_warehouse_id
                 JOIN products p ON p.id=t.product_id
                 WHERE t.status='Chờ xác nhận'"""
        args = ()
        if u["role"] == "Thủ kho":
            sql += " AND t.from_warehouse_id=%s"
            args = (u["warehouse_id"],)
        data = rows(sql + " ORDER BY t.id DESC LIMIT 20", args)
        if not data:
            return "Hiện không có phiếu chuyển kho nào đang chờ trong phạm vi bạn được xem."
        return "Phiếu chuyển kho đang chờ:\n" + "\n".join(
            f"- #{x['id']} {x['from_warehouse']} → {x['to_warehouse']} · {x['product_code']} {x['product_name']} · SL {x['quantity']}"
            for x in data
        )

    # Lô sắp/đã hết hạn
    if "lo" in n and any(k in n for k in ["sap het han", "het han"]) and u["role"] in {"Admin", "Thủ kho"}:
        sql = """SELECT l.code,w.code warehouse,p.code product_code,p.name product_name,l.quantity,l.expiry_date,
                        DATEDIFF(l.expiry_date,CURDATE()) days_left
                 FROM lots l
                 JOIN warehouses w ON w.id=l.warehouse_id
                 JOIN products p ON p.id=l.product_id
                 WHERE l.expiry_date IS NOT NULL AND l.expiry_date<=DATE_ADD(CURDATE(),INTERVAL 30 DAY)"""
        args = ()
        if u["role"] == "Thủ kho":
            sql += " AND l.warehouse_id=%s"
            args = (u["warehouse_id"],)
        data = rows(sql + " ORDER BY l.expiry_date LIMIT 20", args)
        if not data:
            return "Không có lô nào hết hạn hoặc sắp hết hạn trong 30 ngày tới trong phạm vi bạn được xem."
        return "Các lô cần chú ý:\n" + "\n".join(
            f"- {x['code']} · {x['warehouse']} · {x['product_code']} {x['product_name']} · hạn {x['expiry_date']}"
            for x in data
        )

    # Giá trị tồn / nhập / xuất
    if any(k in n for k in ["gia tri ton", "gia tri nhap", "gia tri xuat", "tong gia tri ton"]) and u["role"] in {"Admin", "Kế toán"}:
        r = report_data(u)
        return (
            f"Giá trị tồn: {r['inventory_value']:,.0f} đ; "
            f"giá trị nhập đã xác nhận: {r['import_value']:,.0f} đ; "
            f"giá trị xuất đã xác nhận: {r['export_value']:,.0f} đ."
        )

    # Kho có giá trị tồn lớn nhất
    if "kho nao" in n and "gia tri ton" in n:
        data = rows(
            """SELECT w.code,w.name,SUM(i.quantity*p.price) value
               FROM inventory i JOIN warehouses w ON w.id=i.warehouse_id
               JOIN products p ON p.id=i.product_id
               GROUP BY w.id,w.code,w.name ORDER BY value DESC LIMIT 1"""
        )
        if data:
            x = data[0]
            return f"Kho có giá trị tồn lớn nhất hiện tại là {x['code']} - {x['name']}: {float(x['value'] or 0):,.0f} đ."

    # NCC có giá trị nhập cao nhất
    if "nha cung cap" in n and any(k in n for k in ["cao nhat", "lon nhat", "nhap cao"]):
        data = rows(
            """SELECT COALESCE(s.name,'Không xác định') supplier_name,
                      SUM(t.quantity*t.price) value
               FROM transactions t LEFT JOIN suppliers s ON s.id=t.supplier_id
               WHERE t.status='Đã xác nhận' AND t.type='Nhập'
               GROUP BY s.id,s.name ORDER BY value DESC LIMIT 1"""
        )
        if data:
            x = data[0]
            return f"Nhà cung cấp có giá trị nhập cao nhất hiện tại là {x['supplier_name']}: {float(x['value'] or 0):,.0f} đ."

    # So sánh nhập/xuất 30 ngày
    if "30 ngay" in n and "nhap" in n and "xuat" in n:
        x = row(
            """SELECT
                   COALESCE(SUM(CASE WHEN type='Nhập' THEN quantity ELSE 0 END),0) imp_qty,
                   COALESCE(SUM(CASE WHEN type='Xuất' THEN quantity ELSE 0 END),0) exp_qty,
                   COALESCE(SUM(CASE WHEN type='Nhập' THEN quantity*price ELSE 0 END),0) imp_value,
                   COALESCE(SUM(CASE WHEN type='Xuất' THEN quantity*price ELSE 0 END),0) exp_value
               FROM transactions
               WHERE status='Đã xác nhận' AND confirmed_at>=DATE_SUB(NOW(),INTERVAL 30 DAY)"""
        )
        return (
            f"30 ngày gần đây: nhập {int(x['imp_qty'] or 0)} đơn vị ({float(x['imp_value'] or 0):,.0f} đ); "
            f"xuất {int(x['exp_qty'] or 0)} đơn vị ({float(x['exp_value'] or 0):,.0f} đ)."
        )

    # 10 hoạt động gần nhất
    if "10" in n and "hoat dong" in n and u["role"] == "Admin":
        data = rows(
            """SELECT a.created_at,u.name user_name,a.action,a.target
               FROM audit_logs a LEFT JOIN users u ON u.id=a.user_id
               ORDER BY a.id DESC LIMIT 10"""
        )
        if not data:
            return "Chưa có nhật ký hoạt động."
        return "10 hoạt động gần nhất:\n" + "\n".join(
            f"- {x['created_at']} · {x['user_name'] or 'Hệ thống'} · {x['action']} · {x['target'] or '—'}"
            for x in data
        )

    # Tra sản phẩm cụ thể. Với số lượng sản phẩm của đồ án, một query nhỏ nhanh hơn gọi AI.
    products = rows("SELECT id,code,name,price,sale_price,min_stock FROM products ORDER BY code LIMIT 150")
    for p in products:
        code = normalize(p["code"])
        name = normalize(p["name"])
        if code in n or (len(name) > 4 and name in n):
            sql = """SELECT w.code warehouse,i.quantity
                     FROM inventory i JOIN warehouses w ON w.id=i.warehouse_id
                     WHERE i.product_id=%s"""
            args = [p["id"]]
            if u["role"] == "Thủ kho":
                sql += " AND i.warehouse_id=%s"
                args.append(u["warehouse_id"])
            stocks = rows(sql + " ORDER BY w.code", tuple(args))
            if stocks:
                stock_text = "; ".join(f"{x['warehouse']}: {x['quantity']}" for x in stocks)
                return (
                    f"{p['code']} - {p['name']}: tồn {stock_text}. "
                    f"Giá nhập {float(p['price'] or 0):,.0f} đ; giá bán {float(p['sale_price'] or 0):,.0f} đ; "
                    f"tồn tối thiểu {p['min_stock']}."
                )
            return f"{p['code']} - {p['name']} hiện chưa có dòng tồn trong phạm vi bạn được xem."

    return None


def question_needs_mysql(msg):
    """Câu nghiệp vụ/hướng dẫn thuần túy không cần tải toàn bộ dữ liệu MySQL vào prompt."""
    n = normalize(msg)
    if any(k in n for k in ["quy trinh", "huong dan", "tai sao", "quyen", "phan quyen"]):
        return False
    if any(code.lower() in n for code in RULES):
        return False
    return True


def ai_payload(u):
    """Payload gọn hơn để giảm thời gian query + số token gửi Gemini."""
    invsql = """SELECT w.code warehouse,p.code product_code,p.name product_name,
                        i.quantity,p.min_stock,p.price
                 FROM inventory i JOIN warehouses w ON w.id=i.warehouse_id
                 JOIN products p ON p.id=i.product_id WHERE 1=1"""
    invargs = ()
    if u["role"] == "Thủ kho":
        invsql += " AND i.warehouse_id=%s"
        invargs = (u["warehouse_id"],)

    txsql = """SELECT t.id,t.type,w.code warehouse,p.code product_code,p.name product_name,
                      t.quantity,t.price,t.status,t.confirmed_at,s.name supplier_name
               FROM transactions t JOIN warehouses w ON w.id=t.warehouse_id
               JOIN products p ON p.id=t.product_id
               LEFT JOIN suppliers s ON s.id=t.supplier_id WHERE 1=1"""
    txargs = ()
    if u["role"] == "Thủ kho":
        txsql += " AND t.warehouse_id=%s"
        txargs = (u["warehouse_id"],)

    payload = {
        "role": u["role"],
        "warehouse": u.get("warehouse_code"),
        "allowed_modules": ROLE_PAGES[u["role"]],
        "products": rows(
            """SELECT p.code,p.name,p.unit,p.price,p.sale_price,p.min_stock,p.status,c.name category
               FROM products p LEFT JOIN categories c ON c.id=p.category_id
               ORDER BY p.code LIMIT 100"""
        ),
        "warehouses": rows("SELECT code,name,address,status FROM warehouses ORDER BY code"),
        "inventory": rows(invsql + " ORDER BY w.code,p.code LIMIT 150", invargs),
        "transactions": rows(txsql + " ORDER BY t.id DESC LIMIT 120", txargs),
        "rules": RULES,
    }

    if u["role"] in {"Admin", "Kế toán"}:
        payload["suppliers"] = rows(
            "SELECT code,name,phone,email,address,status FROM suppliers ORDER BY code LIMIT 80"
        )
        payload["reports"] = report_data(u)

    if u["role"] in {"Admin", "Thủ kho"}:
        trsql = """SELECT t.id,wf.code from_warehouse,wt.code to_warehouse,p.code product_code,
                          p.name product_name,t.quantity,t.status,t.confirmed_at
                   FROM transfers t JOIN warehouses wf ON wf.id=t.from_warehouse_id
                   JOIN warehouses wt ON wt.id=t.to_warehouse_id
                   JOIN products p ON p.id=t.product_id WHERE 1=1"""
        trargs = ()
        if u["role"] == "Thủ kho":
            trsql += " AND t.from_warehouse_id=%s"
            trargs = (u["warehouse_id"],)
        payload["transfers"] = rows(trsql + " ORDER BY t.id DESC LIMIT 80", trargs)

        lsql = """SELECT l.code,w.code warehouse,p.code product_code,p.name product_name,
                          l.quantity,l.received_date,l.expiry_date
                   FROM lots l JOIN warehouses w ON w.id=l.warehouse_id
                   JOIN products p ON p.id=l.product_id WHERE 1=1"""
        largs = ()
        if u["role"] == "Thủ kho":
            lsql += " AND l.warehouse_id=%s"
            largs = (u["warehouse_id"],)
        payload["lots"] = rows(lsql + " ORDER BY l.id DESC LIMIT 80", largs)
        payload["alerts"] = build_alerts(u)[:50]

    if u["role"] == "Admin":
        payload["recent_audit"] = rows(
            """SELECT a.created_at,u.name user_name,a.action,a.target,a.detail
               FROM audit_logs a LEFT JOIN users u ON u.id=a.user_id
               ORDER BY a.id DESC LIMIT 20"""
        )

    return payload


@app.post("/api/chat")
def chat():
    try:
        b = request.get_json(silent=True) or {}
        u, err = require_user(b.get("user_id"))
        if err:
            return err

        msg = str(b.get("message", "")).strip()
        if not msg:
            return jsonify(success=False, error="Câu hỏi trống."), 400

        # FAST PATH: câu thường gặp trả lời ngay từ MySQL/RULES, không gọi Gemini.
        local = fast_local_answer(u, msg)
        if local:
            return jsonify(success=True, answer=local, source="mysql-fast")

        # RAG: chỉ lấy 3 đoạn gần nhất; rag.py có cache cho câu hỏi lặp lại.
        knowledge_context = search_knowledge(msg, n_results=3)
        history = b.get("history") or []

        # Câu nghiệp vụ thuần túy không cần kéo một payload MySQL lớn.
        mysql_data = ai_payload(u) if question_needs_mysql(msg) else {
            "role": u["role"],
            "warehouse": u.get("warehouse_code"),
            "allowed_modules": ROLE_PAGES[u["role"]],
            "rules": RULES,
        }

        prompt = {
            "USER_ROLE": u["role"],
            "USER_WAREHOUSE": u.get("warehouse_code"),
            "QUESTION": msg,
            "RECENT_CONVERSATION": history[-6:],
            "RAG_KNOWLEDGE": knowledge_context,
            "MYSQL_DATA": mysql_data,
        }

        if not client:
            if knowledge_context:
                return jsonify(success=True, answer=knowledge_context, source="rag-fast")
            return jsonify(success=False, error="Chưa có GEMINI_API_KEY trong backend/.env."), 500

        try:
            r = client.models.generate_content(
                model=MODEL,
                contents=json.dumps(prompt, ensure_ascii=False, default=str),
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM,
                    temperature=0.1,
                    max_output_tokens=800,
                ),
            )
            answer = (r.text or "").strip()
            return jsonify(
                success=True,
                answer=answer or "Chưa đủ dữ liệu để phân tích.",
                model=MODEL,
                source="gemini",
            )

        except Exception as e:
            error_text = str(e)
            upper = error_text.upper()

            # Không sleep/retry 2s + 4s: lỗi API được phản hồi ngay để giao diện không treo.
            if "503" in error_text or "UNAVAILABLE" in upper or "OVERLOADED" in upper:
                if knowledge_context:
                    return jsonify(
                        success=True,
                        answer=knowledge_context + "\n\n(Gemini đang bận; đây là kết quả tra cứu nhanh từ RAG.)",
                        source="rag-fallback",
                    )
                return jsonify(success=False, error="Gemini đang bận. Hãy thử lại."), 503

            if "429" in error_text or "RESOURCE_EXHAUSTED" in upper or "QUOTA" in upper:
                if knowledge_context:
                    return jsonify(
                        success=True,
                        answer=knowledge_context + "\n\n(Gemini đang chạm giới hạn; đây là kết quả tra cứu nhanh từ RAG.)",
                        source="rag-fallback",
                    )
                return jsonify(success=False, error="Gemini đang chạm giới hạn yêu cầu. Hãy thử lại sau."), 429

            if "404" in error_text or "NOT_FOUND" in upper:
                return jsonify(
                    success=False,
                    error=f"Model Gemini hiện không khả dụng: {MODEL}. Kiểm tra GEMINI_MODEL trong backend/.env.",
                ), 404

            if "401" in error_text or "403" in error_text or "API_KEY" in upper or "API KEY" in upper:
                return jsonify(
                    success=False,
                    error="API Key Gemini không hợp lệ hoặc chưa được cấp quyền. Kiểm tra backend/.env.",
                ), 401

            raise

    except Exception as e:
        return jsonify(success=False, error=str(e)), 500


if __name__=="__main__":
    try:
        init_db()
        print("="*66)
        print("KHOAI BACKEND ĐÃ SẴN SÀNG")
        print("MySQL database:",DB_NAME)
        print("API: http://127.0.0.1:5000/api/health")
        print("Tài khoản mẫu: admin@khoai.vn / 123456")
        print("="*66)
        app.run(host="127.0.0.1",port=5000,debug=False)
    except Exception as e:
        print("\nKHÔNG KHỞI ĐỘNG ĐƯỢC KHOAI:",e)
        print("Kiểm tra MySQL đang chạy và DB_USER/DB_PASSWORD trong backend/.env.")
