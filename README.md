# KhoAI đầy đủ 3 vai trò + MySQL + Gemini

## Tài khoản mẫu
- Admin: admin@khoai.vn / 123456
- Thủ kho: thukho@khoai.vn / 123456
- Kế toán: ketoan@khoai.vn / 123456

## Chạy backend
```powershell
cd backend
.\venv\Scripts\Activate.ps1
python -m pip install -r ..\requirements.txt
python app.py
```

## Chạy frontend
Mở `index.html` bằng Live Server.

## Lưu ý
- Sao chép `.env.example` thành `backend/.env` rồi sửa DB_PASSWORD cho đúng mật khẩu MySQL root.
- Chatbot không huấn luyện lại mô hình Gemini; backend mở rộng dữ liệu ngữ cảnh, lịch sử hội thoại, prompt và fallback MySQL để trả lời nhiều dạng câu hỏi hơn.
