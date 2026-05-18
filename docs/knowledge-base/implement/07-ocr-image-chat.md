# 07 — OCR & Image Chat

**Mức độ phụ thuộc:** Cần hoàn thành 01–06 trước.  
**Phạm vi ảnh hưởng:** `iccp_be_ai` (chính), `iccp_web` (FE)

---

## Tổng quan

File này cover **2 luồng OCR độc lập**:

| Luồng | Trigger | Công cụ | Mục đích |
|---|---|---|---|
| **A. Document OCR** | Khi ingest tài liệu | Tesseract (self-hosted) | Đọc text từ PDF scan / image-heavy |
| **B. Image Chat OCR** | User gửi ảnh trong chat | Gemini Vision API | Trả lời câu hỏi dựa trên ảnh |

---

## Luồng A — Document OCR (Scanned PDF)

### Vấn đề

`fitz.get_text("text")` trả về chuỗi rỗng khi PDF là bản scan (chụp ảnh / in rồi scan lại).  
File `doc`/`docx` chứa embedded image thay vì text cũng có vấn đề tương tự.

### Giải pháp

1. **Thử text extraction trước** (`fitz`)
2. **Detect scanned**: nếu `avg_chars_per_page < 50` → coi là scan
3. **Fallback Tesseract**: dùng `pdf2image` → convert PDF pages → PIL images → `pytesseract`

### A1. Cài dependencies

**`requirements.txt`** — thêm:
```
pytesseract>=0.3.13
pdf2image>=1.17.0
openpyxl>=3.1.5
chardet>=5.2.0
```

**`Dockerfile`** — đã cập nhật (apt packages):
```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl \
    tesseract-ocr \
    tesseract-ocr-vie \
    tesseract-ocr-eng \
    poppler-utils \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*
```

> **Note:** `poppler-utils` cung cấp `pdftoppm` — công cụ mà `pdf2image` sử dụng bên dưới.

### A2. FileParserService — đã cập nhật

File `app/services/file_parser_service.py` đã được refactor với logic sau:

```python
# Constant kiểm soát ngưỡng detect scanned
OCR_TEXT_THRESHOLD = 50  # chars per page

# Allowed file types (validate ở ingest API)
ALLOWED_FILE_TYPES = {"pdf", "doc", "docx", "xlsx", "xls", "txt", "md", "markdown"}
```

**Logic PDF parsing:**
```
_parse_pdf(file_path)
  ├── fitz.get_text() từng trang
  ├── Trang nào < 50 chars → đánh dấu SCANNED
  ├── Nếu > 50% trang scanned → _ocr_pdf() toàn bộ
  └── Nếu < 50% trang scanned → _ocr_pdf_pages() chỉ những trang đó
```

**Excel parsing** (mới):
```
_parse_excel(file_path)
  └── openpyxl → iter rows → join cells với " | "
      Format output:
      [Sheet: Sheet1]
      Col A | Col B | Col C
      val1 | val2 | val3
```

**parse_bytes() method** (mới — dùng khi download từ ImageKit):
```python
await FileParserService.parse_bytes(data=raw_bytes, file_name="doc.pdf", mime_type="application/pdf")
```
→ Ghi ra tempfile → parse → xóa tempfile

### A3. Kiểm tra trong IngestionAgent

`IngestionAgent.run()` gọi `FileParserService.parse(input.file_path, input.file_type)`.  
Không cần thay đổi — OCR detection xảy ra tự động bên trong `_parse_pdf()`.

**Nếu kết quả OCR rỗng** (`raw_text.strip() == ""`):
```python
if not raw_text.strip():
    raise IngestionException(f"Parsed empty content from {input.file_path}")
```
→ Job sẽ fail, status `failed`, thông báo cho user qua notification.

---

## Luồng B — Image Chat (User gửi ảnh vào chat)

### Tại sao dùng Gemini Vision thay vì Tesseract?

| | Tesseract | Gemini Vision |
|---|---|---|
| Loại | OCR thuần túy — chỉ trích text | Multimodal LLM — hiểu nội dung |
| Phù hợp | Documents scan | Chat hỏi đáp về ảnh |
| Output | Raw text | Câu trả lời ngữ nghĩa |
| Cost | Free (self-hosted) | Tính theo token |

→ Chat cần Gemini Vision vì user hỏi *"Ảnh này nói gì?"*, *"Biểu đồ này có xu hướng gì?"*, không chỉ cần text thô.

### B1. Luồng upload ảnh

```
User chọn ảnh (FE)
  └─→ POST /v1/chat/upload-image (multipart/form-data)
        ├── BE AI upload lên ImageKit → nhận url
        └── Trả về { url, mime_type, original_name }

User nhấn Send (với image url + text content)
  └─→ POST /v1/conversations/{id}/messages
        Body JSON:
        {
          "content": "Ảnh này nói về gì?",
          "image": {
            "url": "https://ik.imagekit.io/.../img.jpg",
            "mime_type": "image/jpeg",
            "original_name": "diagram.jpg"
          }
        }
```

### B2. Endpoint upload ảnh (`iccp_be_ai`)

**File:** `app/api/v1/messages.py` hoặc tạo `app/api/v1/chat_upload.py`

```python
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from app.core.security import get_current_user, TokenPayload
from app.clients.imagekit_client import ImageKitClient

router = APIRouter()

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_IMAGE_SIZE_MB = 5

@router.post("/chat/upload-image")
async def upload_chat_image(
    file: UploadFile = File(...),
    current_user: TokenPayload = Depends(get_current_user),
):
    # Validate MIME type
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(400, f"Chỉ hỗ trợ: {', '.join(ALLOWED_IMAGE_TYPES)}")

    # Validate file size
    contents = await file.read()
    if len(contents) > MAX_IMAGE_SIZE_MB * 1024 * 1024:
        raise HTTPException(400, f"Ảnh không được lớn hơn {MAX_IMAGE_SIZE_MB}MB")

    # Upload lên ImageKit
    result = await ImageKitClient.upload(
        data=contents,
        file_name=file.filename or "chat_image",
        folder=f"/chat/{current_user.organization_id}",
    )

    return {
        "url": result["url"],
        "mime_type": file.content_type,
        "original_name": file.filename,
    }
```

### B3. OrchestratorInput — thêm image

**File:** `app/agents/orchestrator/orchestrator.py`

```python
@dataclass
class OrchestratorInput(AgentInput):
    conversation_id: str = ""
    message_id: str = ""
    content: str = ""
    # Ảnh đính kèm (optional)
    image_url: Optional[str] = None
    image_mime_type: Optional[str] = None
    # ... các field khác giữ nguyên
```

### B4. GeminiVisionService — xử lý ảnh trong chat

**File mới:** `app/services/gemini_vision_service.py`

```python
"""
GeminiVisionService
===================
Dùng Gemini multimodal để phân tích ảnh user gửi trong chat.
Trả về text description / answer để dùng làm context cho LLM.
"""
from __future__ import annotations

import base64
from typing import Optional

import httpx
import structlog

from app.core.config import settings

log = structlog.get_logger(__name__)


class GeminiVisionService:

    @classmethod
    async def describe_image(
        cls,
        image_url: str,
        user_question: str,
        mime_type: str = "image/jpeg",
    ) -> str:
        """
        Tải ảnh từ URL → gửi lên Gemini Vision → trả về mô tả/trả lời.

        Returns:
            str: Mô tả nội dung ảnh (dùng làm context cho RAG)
        """
        # Download ảnh
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(image_url)
            resp.raise_for_status()
            image_data = base64.standard_b64encode(resp.content).decode("utf-8")

        # Gọi Gemini Vision (gemini-2.5-flash-preview-04-17)
        payload = {
            "contents": [
                {
                    "parts": [
                        {
                            "inline_data": {
                                "mime_type": mime_type,
                                "data": image_data,
                            }
                        },
                        {
                            "text": (
                                f"Người dùng hỏi: {user_question}\n\n"
                                "Hãy mô tả nội dung ảnh một cách chi tiết, "
                                "tập trung vào những gì liên quan đến câu hỏi trên. "
                                "Nếu ảnh chứa text hoặc số liệu, hãy trích xuất đầy đủ."
                            )
                        },
                    ]
                }
            ],
            "generationConfig": {
                "maxOutputTokens": 1024,
                "temperature": 0.1,
            },
        }

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"gemini-2.5-flash-preview-04-17:generateContent"
                f"?key={settings.GEMINI_API_KEY}",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        # Extract text từ response
        try:
            vision_text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as e:
            log.error("gemini_vision.parse_error", response=data, error=str(e))
            return "[Không thể phân tích ảnh]"

        log.info(
            "gemini_vision.described",
            image_url=image_url,
            chars=len(vision_text),
        )
        return vision_text
```

### B5. Orchestrator — tích hợp image context

Khi `image_url` có trong input, Orchestrator gọi Gemini Vision trước → thêm mô tả ảnh vào context:

```python
# Trong OrchestratorAgent._build_context() hoặc run()

image_context = ""
if input.image_url:
    try:
        image_context = await GeminiVisionService.describe_image(
            image_url=input.image_url,
            user_question=input.content,
            mime_type=input.image_mime_type or "image/jpeg",
        )
        log.info("orchestrator.image_described", chars=len(image_context))
    except Exception as e:
        log.warning("orchestrator.image_describe_failed", error=str(e))
        image_context = ""

# Thêm vào system prompt hoặc user message
if image_context:
    enhanced_content = (
        f"{input.content}\n\n"
        f"[Nội dung ảnh đính kèm]\n{image_context}"
    )
else:
    enhanced_content = input.content
```

### B6. Lưu ảnh vào MessageSchema (MongoDB)

**File:** `app/db/schemas/message.py` — đã cập nhật:

```python
class ImageAttachmentSchema(BaseModel):
    url: str
    mime_type: str = "image/jpeg"
    original_name: Optional[str] = None
    ocr_text: Optional[str] = None  # Cache Gemini Vision output

class MessageSchema(BaseModel):
    # ... các field khác
    image: Optional[ImageAttachmentSchema] = None  # NEW
```

**Lưu message với ảnh:**
```python
user_msg = MessageSchema(
    conversation_id=conversation_id,
    organization_id=organization_id,
    user_id=user_id,
    role="user",
    content=request.content,
    image=ImageAttachmentSchema(
        url=request.image.url,
        mime_type=request.image.mime_type,
        original_name=request.image.original_name,
        ocr_text=image_context,  # Cache để tránh gọi lại Gemini
    ) if request.image else None,
)
await message_repo.create(user_msg)
```

### B7. messages.py endpoint — handle image

**File:** `app/api/v1/messages.py`

```python
@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: str,
    request: SendMessageRequest,
    current_user: TokenPayload = Depends(get_current_user),
    # ...
):
    # Build orchestrator input
    orch_input = OrchestratorInput(
        organization_id=current_user.organization_id,
        user_id=current_user.user_id,
        conversation_id=conversation_id,
        content=request.content,
        # Pass image info nếu có
        image_url=request.image.url if request.image else None,
        image_mime_type=request.image.mime_type if request.image else None,
        # ...
    )
    # Phần còn lại giữ nguyên
```

---

## Luồng B — FE Integration (`iccp_web`)

### B-FE1. Upload ảnh (two-step)

```typescript
// 1. Upload ảnh lên BE AI trước khi gửi message
async function uploadChatImage(file: File): Promise<ImageAttachment> {
  const form = new FormData();
  form.append('file', file);
  
  const res = await fetch(`${AI_BASE_URL}/v1/chat/upload-image`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
    body: form,
  });
  
  if (!res.ok) throw new Error('Upload ảnh thất bại');
  return res.json(); // { url, mime_type, original_name }
}

// 2. Gửi message kèm image url
const payload: SendMessageRequest = {
  content: textInput,
  image: uploadedImage ?? undefined,
};
```

### B-FE2. Hiển thị ảnh trong chat history

```tsx
// components/chat/ChatMessage.tsx
interface ChatMessageProps {
  message: MessageResponse;
}

export function ChatMessage({ message }: ChatMessageProps) {
  return (
    <div className={cn("flex gap-3", message.role === "user" ? "justify-end" : "justify-start")}>
      <div className="max-w-[70%] space-y-2">
        {/* Hiển thị ảnh nếu có (chỉ user message) */}
        {message.image && (
          <div className="rounded-lg overflow-hidden border">
            <img
              src={message.image.url}
              alt={message.image.original_name ?? "Ảnh đính kèm"}
              className="max-w-full max-h-80 object-contain"
            />
          </div>
        )}
        {/* Text content */}
        <div className="rounded-lg px-4 py-2 bg-primary text-primary-foreground">
          {message.content}
        </div>
      </div>
    </div>
  );
}
```

### B-FE3. Image picker trong chat input

```tsx
// components/chat/ChatInput.tsx
const [pendingImage, setPendingImage] = useState<ImageAttachment | null>(null);
const [uploading, setUploading] = useState(false);

const handleImageSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
  const file = e.target.files?.[0];
  if (!file) return;

  // Validate client-side
  const allowed = ["image/jpeg", "image/png", "image/webp"];
  if (!allowed.includes(file.type)) {
    toast.error("Chỉ hỗ trợ JPEG, PNG, WebP");
    return;
  }
  if (file.size > 5 * 1024 * 1024) {
    toast.error("Ảnh không được lớn hơn 5MB");
    return;
  }

  setUploading(true);
  try {
    const attachment = await uploadChatImage(file);
    setPendingImage(attachment);
  } catch {
    toast.error("Upload ảnh thất bại");
  } finally {
    setUploading(false);
  }
};

// Trong form submit:
const handleSend = async () => {
  await sendMessage({
    content: text,
    image: pendingImage ?? undefined,
  });
  setPendingImage(null);
};
```

---

## Kiểm tra integration tổng thể

### Checklist document OCR

- [ ] Rebuild Docker image (`docker-compose up --build`)
- [ ] `docker exec <container> tesseract --version` → phải hiện version
- [ ] `docker exec <container> tesseract --list-langs` → phải có `vie`, `eng`
- [ ] Upload 1 PDF scan → job status chuyển `indexed` → check chunks có text không rỗng
- [ ] Upload 1 Excel `.xlsx` → check chunks lấy được dữ liệu từng sheet

### Checklist image chat

- [ ] `POST /v1/chat/upload-image` với JPEG → nhận `url` trả về từ ImageKit
- [ ] `POST /v1/conversations/{id}/messages` với `image` field → response có citations từ RAG
- [ ] `GET /v1/conversations/{id}/messages` → message history có `image.url` trong user messages
- [ ] FE: chọn ảnh → upload spinner → thumbnail preview → gửi → ảnh hiển thị trong bubble
- [ ] FE: load history → ảnh cũ vẫn hiển thị đúng

---

## Lưu ý bảo mật

1. **Validate MIME type server-side**: Không tin tưởng `Content-Type` từ client, dùng `python-magic` để detect thực.
2. **Giới hạn size**: Chặn ở 5MB để tránh OOM trong Gemini Vision request.
3. **Rate limit**: Image chat nên có rate limit riêng (e.g., 10 ảnh/phút/user) vì cost cao hơn text.
4. **Không lưu ảnh trong MongoDB**: Chỉ lưu URL. Raw bytes không được lưu vào DB.
5. **OCR text caching**: `image.ocr_text` trong `MessageSchema` cache kết quả Gemini Vision. Không gọi Gemini lại khi load history.

---

## Phụ thuộc thêm

```
# requirements.txt — thêm các dòng này
pytesseract>=0.3.13       # Python wrapper cho Tesseract CLI
pdf2image>=1.17.0         # PDF → PIL image (dùng poppler)
openpyxl>=3.1.5           # Excel parser
python-magic>=0.4.27      # File type detection (cần libmagic1 trong Docker)
```
