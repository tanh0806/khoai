import json
import os
from getpass import getpass
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS
from google import genai
from google.genai import types

try:
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
except ImportError:
    pass

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"


def ensure_api_key() -> str:
    """Đọc key từ .env. Nếu chưa có, hỏi ngay trong Terminal và tự lưu."""
    load_dotenv(ENV_FILE)
    key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if key and key not in {"PASTE_KEY_HERE", "YOUR_API_KEY"}:
        return key

    print("\n" + "=" * 62)
    print("KHOAI - CẤU HÌNH GEMINI LẦN ĐẦU")
    print("Dán Gemini API Key của bạn bên dưới.")
    print("Key sẽ được lưu cục bộ vào backend/.env và không đưa ra frontend.")
    print("=" * 62)
    key = getpass("GEMINI_API_KEY: ").strip()
    if not key:
        raise RuntimeError("Bạn chưa nhập GEMINI_API_KEY.")

    ENV_FILE.write_text(f"GEMINI_API_KEY={key}\n", encoding="utf-8")
    os.environ["GEMINI_API_KEY"] = key
    print("✓ Đã lưu API key vào backend/.env\n")
    return key


API_KEY = ensure_api_key()
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.7-flash").strip() or "gemini-3.7-flash"
FALLBACK_MODELS = [m.strip() for m in os.getenv("GEMINI_FALLBACK_MODELS", "gemini-3.6-flash,gemini-3.5-flash").split(",") if m.strip()]
client = genai.Client(api_key=API_KEY)

app = Flask(__name__)
CORS(app)

SYSTEM_INSTRUCTION = """
Bạn là KhoAI, trợ lý AI của hệ thống quản lý kho tích hợp Gemini.

MỤC TIÊU:
- Trò chuyện tự nhiên như một trợ lý AI thông thường.
- Giải thích kiến thức quản lý kho, quy trình nghiệp vụ và cách sử dụng hệ thống.
- Khi người dùng hỏi số liệu thực tế của kho, phải trả lời dựa trên dữ liệu JSON được cung cấp.
- Hỗ trợ báo cáo nhập - xuất - tồn, cảnh báo, dự báo và gợi ý nhập hàng.

HÃY TỰ PHÂN LOẠI CÂU HỎI THÀNH 1 TRONG 3 NHÓM:

A. HỘI THOẠI / KIẾN THỨC THÔNG THƯỜNG
Ví dụ: "hi", "xin chào", "bạn là ai", "bạn làm được gì", "cảm ơn", hoặc câu hỏi kiến thức phổ thông.
- Trả lời tự nhiên, thân thiện bằng tiếng Việt.
- Không được trả lời "Chưa đủ dữ liệu" chỉ vì câu hỏi không liên quan số liệu kho.

B. KIẾN THỨC NGHIỆP VỤ KHO
Ví dụ: "quy trình nhập kho là gì", "kiểm kê dùng để làm gì", "tồn tối thiểu là gì", "AI dự báo nhu cầu như thế nào".
- Có thể trả lời bằng kiến thức chung về quản lý kho.
- Không được bịa số liệu cụ thể của hệ thống nếu dữ liệu JSON không có.
- Nếu đang giải thích kiến thức chung, nói rõ đây là giải thích nghiệp vụ, không phải số liệu thực tế của kho.

C. CÂU HỎI DỮ LIỆU THỰC TẾ CỦA KHO
Ví dụ: "SP003 còn bao nhiêu", "tháng này đã xuất bao nhiêu", "kho nào sắp hết", "tôi muốn biết tình hình kho của mình", "gợi ý nhập hàng".
- Phải dùng dữ liệu JSON trong yêu cầu hiện tại.
- Chỉ coi giao dịch có trạng thái "Đã xác nhận" là dữ liệu nhập/xuất hợp lệ.
- Nếu có RULE_ENGINE_VERIFIED_SUMMARY thì các con số trong đó là nguồn ưu tiên cao nhất, không được tự sửa.
- Không tự bịa mã hàng, số lượng, giá, ngày, nhà cung cấp hoặc giao dịch.
- Chỉ nói "Chưa đủ dữ liệu để phân tích" khi câu hỏi thực sự yêu cầu một số liệu/kết luận mà dữ liệu hiện tại không cung cấp.
- Với câu hỏi rộng như "cho tôi biết thêm về kho hàng của mình", hãy chủ động tóm tắt những gì có trong dữ liệu: số kho, số mặt hàng, tổng tồn, mặt hàng tồn thấp/hết, giao dịch gần đây và điểm cần chú ý trong phạm vi quyền của người dùng. Không từ chối chỉ vì câu hỏi rộng.

NGỮ CẢNH HỘI THOẠI:
- Có thể sử dụng CONVERSATION_HISTORY để hiểu câu hỏi nối tiếp như "còn cái nào nữa?", "giá bao nhiêu?", "ở kho nào?", "tại sao?".
- Ngữ cảnh trước đó chỉ giúp hiểu ý người dùng, không được dùng để thay thế dữ liệu kho hiện tại.

PHÂN QUYỀN:
- Thủ kho: được hỏi và phân tích dữ liệu của kho được phân công trong JSON; có quyền nghiệp vụ thêm và tự duyệt hàng hóa mới trong kho phụ trách.
- Kế toán: được phân tích dữ liệu giao dịch, đối chiếu và báo cáo; không tự ý sửa tồn kho.
- Admin: quản trị hệ thống và được phân tích toàn bộ dữ liệu được cung cấp.
- Chỉ dùng đúng vai trò nhận từ frontend: Admin, Thủ kho hoặc Kế toán. Không áp dụng vai trò Khách/Nhân viên/Quản lý cũ.

CHỨC NĂNG AI KHO:
1. Tra cứu hàng hóa theo mã, tên, danh mục.
2. Tra cứu tồn kho và vị trí.
3. Hỏi giá bán tham khảo.
4. Hỏi lịch sử nhập/xuất.
5. Hỏi tốc độ xuất theo thời gian.
6. Báo cáo nhập - xuất - tồn.
7. Cảnh báo tồn thấp và hết hàng.
8. Phân tích lô gần hết hạn/hết hạn nếu có dữ liệu.
9. Tóm tắt tình hình kho.
10. Phát hiện biến động đáng chú ý.
11. Phát hiện hàng tồn lâu.
12. Dự báo nhu cầu khi đủ dữ liệu.
13. Gợi ý mặt hàng cần nhập.
14. Gợi ý số lượng nhập.
15. Giải thích lý do khuyến nghị.
16. Giải thích quy trình nhập, xuất, chuyển kho, kiểm kê và mã QR.
17. Hướng dẫn sử dụng các chức năng của hệ thống.

CÔNG THỨC THAM KHẢO:
Số lượng đề xuất nhập = Nhu cầu dự kiến + Mức tồn an toàn - Tồn hiện tại.

AN TOÀN NGHIỆP VỤ:
- AI chỉ đọc, phân tích, báo cáo và khuyến nghị.
- AI không tự nhập, xuất, chuyển, xóa hoặc chỉnh sửa tồn kho.
- AI không tự thực hiện nghiệp vụ; chỉ người dùng có quyền phù hợp mới được thao tác trên website.

CÁCH TRẢ LỜI:
- Luôn dùng tiếng Việt.
- Tự nhiên, dễ hiểu, ưu tiên trả lời thẳng câu hỏi.
- Với câu hỏi đơn giản, trả lời ngắn gọn.
- Với báo cáo, dùng tiêu đề và gạch đầu dòng/bảng văn bản dễ đọc.
- Nếu câu hỏi hơi mơ hồ nhưng vẫn có thể trả lời hữu ích, hãy trả lời phần có thể xác định trước rồi mới đề nghị người dùng hỏi cụ thể hơn; không từ chối ngay.
""".strip()


@app.get("/")
def home():
    return jsonify(
        status="ok",
        service="KhoAI Gemini Backend",
        model=MODEL,
        message="Backend đã sẵn sàng. Website có thể gọi /api/chat.",
    )


@app.get("/api/health")
def health():
    return jsonify(success=True, model=MODEL)


@app.post("/api/chat")
def chat():
    try:
        body = request.get_json(silent=True) or {}
        message = str(body.get("message", "")).strip()
        role = str(body.get("role", "Khách")).strip()
        warehouse = str(body.get("warehouse", "")).strip()
        data = body.get("data", {})
        verified = str(body.get("verified_summary", "")).strip()
        history_raw = body.get("history", [])
        history = []
        if isinstance(history_raw, list):
            for item in history_raw[-12:]:
                if not isinstance(item, dict):
                    continue
                r = str(item.get("role", "")).strip()
                t = str(item.get("text", "")).strip()[:1200]
                if r in {"user", "assistant"} and t:
                    history.append({"role": r, "text": t})

        if not message:
            return jsonify(success=False, error="Câu hỏi không được để trống."), 400
        if len(message) > 3000:
            return jsonify(success=False, error="Câu hỏi quá dài."), 400
        # Hỗ trợ nhãn vai trò cũ để tránh lỗi khi frontend/backend khác phiên bản.
        role_aliases = {"Nhân viên": "Thủ kho", "Quản lý": "Admin", "Quản trị viên": "Admin"}
        role = role_aliases.get(role, role)
        if role not in {"Admin", "Thủ kho", "Kế toán"}:
            return jsonify(success=False, error=f"Vai trò không hợp lệ: {role}. Hãy dùng Admin, Thủ kho hoặc Kế toán."), 400

        payload = {
            "ROLE": role,
            "ASSIGNED_WAREHOUSE": warehouse or None,
            "QUESTION": message,
            "CONVERSATION_HISTORY": history,
            "RULE_ENGINE_VERIFIED_SUMMARY": verified or None,
            "WAREHOUSE_DATA": data,
        }

        contents = json.dumps(payload, ensure_ascii=False, indent=2)
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            temperature=0.35,
            max_output_tokens=1800,
        )
        response = None
        models_to_try = list(dict.fromkeys([MODEL] + FALLBACK_MODELS))
        last_error = None
        for model_name in models_to_try:
            try:
                response = client.models.generate_content(
                    model=model_name, contents=contents, config=config
                )
                break
            except Exception as model_exc:
                last_error = model_exc
                error_text = str(model_exc).lower()
                if not any(x in error_text for x in ("404", "not found", "503", "unavailable", "429", "resource_exhausted")):
                    raise
                print(f"Gemini model {model_name} failed; trying fallback if available: {model_exc!r}")
        if response is None:
            raise last_error or RuntimeError("Không có model Gemini khả dụng.")

        answer = (response.text or "").strip()
        if not answer:
            answer = "Chưa đủ dữ liệu để phân tích."
        return jsonify(success=True, answer=answer, model=MODEL)

    except Exception as exc:
        print("Gemini error:", repr(exc))
        text = str(exc)
        if "API_KEY" in text.upper() or "API KEY" in text.upper() or "401" in text or "403" in text:
            text = "API Key Gemini không hợp lệ hoặc chưa được cấp quyền. Hãy kiểm tra key trong backend/.env."
        return jsonify(success=False, error=text), 500


if __name__ == "__main__":
    print("=" * 62)
    print("KHOAI GEMINI BACKEND ĐANG CHẠY")
    print(f"Model: {MODEL}")
    print("Địa chỉ: http://127.0.0.1:5000")
    print("Giữ nguyên Terminal này trong lúc dùng website.")
    print("=" * 62)
    app.run(host="127.0.0.1", port=5000, debug=False)
