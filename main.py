import lark_oapi as lark
from lark_oapi.api.bitable.v1 import *
from lark_oapi.api.drive.v1 import *
import requests
import base64
import io
from PIL import Image, ImageOps
import time
import os

# ================= 🟢 环境变量配置 (云端安全模式) =================
APP_ID = os.getenv("APP_ID")
APP_SECRET = os.getenv("APP_SECRET")
APP_TOKEN = os.getenv("APP_TOKEN")
TABLE_ID = os.getenv("TABLE_ID")
AI_API_KEY = os.getenv("AI_API_KEY")

# 🔗 AI 服务地址
AI_API_BASE = "https://x666.me/v1/chat/completions"

# 🤖 模型选择
AI_MODEL = "gemini-2.5-flash"

# 📋 字段映射配置
FIELD_IMG = "上传作文图片"      
FIELD_RESULT = "评语"          
FIELD_STATUS = "单选"          
STATUS_TODO = "未完成"         
STATUS_DONE = "已完成"         
# ==========================================================

# 初始化飞书客户端
client = lark.Client.builder().app_id(APP_ID).app_secret(APP_SECRET).build()

def compress_image(image_binary, max_side=1024, quality=60):
    """ 图片压缩：限制长边 1024px，转 JPEG 压缩质量 60，且自动扶正方向 """
    try:
        img = Image.open(io.BytesIO(image_binary))
        img = ImageOps.exif_transpose(img) # 自动扶正
        if img.mode in ("RGBA", "P"): img = img.convert("RGB")
        
        w, h = img.size
        if max(w, h) > max_side:
            scale = max_side / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
            
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=quality, optimize=True)
        return base64.b64encode(buffer.getvalue()).decode('utf-8')
    except Exception as e:
        print(f"   ❌ 图片压缩出错: {e}")
        return None

def call_ai_api_with_retry(image_b64_list, prompt, max_retries=3, temperature=0.2):
    """ 
    🛡️ 带重试机制 + 温度控制的 API 调用 
    temperature=0.2: 让 AI 极度冷静，严格遵守逻辑，减少 OCR 幻觉
    """
    headers = {"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"}
    
    content_list = [{"type": "text", "text": prompt}]
    for b64 in image_b64_list:
        content_list.append({
            "type": "image_url", 
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"}
        })

    payload = {
        "model": AI_MODEL,
        "messages": [{"role": "user", "content": content_list}],
        "temperature": temperature  # 👈 核心修改：加入温度控制
    }

    for attempt in range(max_retries):
        try:
            if attempt > 0: print(f"   🔄 第 {attempt+1} 次重试连接 AI...")
            
            # 调试打印（可选）
            # print(f"   🧠 发送请求... 温度: {temperature}")

            resp = requests.post(AI_API_BASE, json=payload, headers=headers, timeout=60)
            
            if resp.status_code == 200:
                return resp.json().get('choices', [{}])[0].get('message', {}).get('content', '')
            
            elif resp.status_code in [503, 429, 500, 502, 504]:
                wait_time = 5 * (attempt + 1)
                print(f"   ⚠️ 服务拥堵 (Code {resp.status_code})，休息 {wait_time} 秒...")
                time.sleep(wait_time)
                continue 
            else:
                print(f"   ❌ API 错误: {resp.status_code} - {resp.text}")
                return None
                
        except requests.exceptions.RequestException as e:
            print(f"   ⚠️ 网络波动: {e}，准备重试...")
            time.sleep(3)
            
    print("   ❌ 重试 3 次均失败，跳过此条。")
    return None

def main():
    print("🚀 云端脚本启动 (GitHub Actions - v7.5 严谨防御版)...")
    
    if not APP_ID or not AI_API_KEY:
        print("❌ 错误：未读取到环境变量，请检查 GitHub Secrets 配置！")
        return

    # 1. 查找状态为“未完成”的记录
    filter_cmd = f'CurrentValue.[{FIELD_STATUS}] = "{STATUS_TODO}"'
    req = ListAppTableRecordRequest.builder() \
        .app_token(APP_TOKEN).table_id(TABLE_ID) \
        .filter(filter_cmd).build()

    resp = client.bitable.v1.app_table_record.list(req)
    if not resp.success():
        print(f"❌ 获取记录失败: {resp.msg}")
        return

    records = resp.data.items
    if not records:
        print("✅ 所有作业都已批改完成！")
        return

    print(f"📋 发现 {len(records)} 份待批改作业。")

    for i, record in enumerate(records):
        rec_id = record.record_id
        fields = record.fields
        student_name = fields.get("学生姓名", "未知学生")
        
        print(f"\n[{i+1}/{len(records)}] 正在批改 {student_name} 的作业...")

        img_list = fields.get(FIELD_IMG)
        if not img_list:
            print("   ⚠️ 未上传图片，跳过。")
            continue
            
        # 2. 下载并处理图片
        b64_images = []
        all_downloads_success = True
        
        for img_info in img_list:
            file_token = img_info['file_token']
            print(f"   ⬇️ 下载图片...", end="", flush=True)
            
            down_req = DownloadMediaRequest.builder().file_token(file_token).build()
            down_resp = client.drive.v1.media.download(down_req)
            
            if down_resp.success():
                b64 = compress_image(down_resp.file.read())
                if b64: 
                    b64_images.append(b64)
                    print(" OK")
                else:
                    print(" 压缩失败")
                    all_downloads_success = False
                    break 
            else:
                print(f" 失败 ({down_resp.msg})")
                all_downloads_success = False
                break 
        
        if not all_downloads_success:
            print("   ⛔ 存在图片下载/处理失败，为防止误判，跳过此学生。")
            continue 
            
        if not b64_images:
            print("   ⚠️ 图片列表为空，跳过。")
            continue

        # 3. 调用 AI (注入 v7.5 终极提示词)
        print(f"   🧠 AI ({AI_MODEL}) 正在思考 ({len(b64_images)} 图)...")
        
        # 👇👇👇 v7.5 提示词 (含正误案例 Few-Shot) 👇👇👇
        prompt = """
# 小学作文批改主控指令 (Master Prompt) v7.5
# 更新：集成正误案例对比 (Few-Shot) + OCR 强容错

## Role (角色设定)
你是一位拥有 20 年教龄的小学语文老师，同时也是一位**“OCR 幻觉审核员”**。
你的核心任务是：
1.  **过滤机器错误**：识别并自动忽略 OCR 产生的识别乱码（如连笔识别成多字、横线识别成片假名）。
2.  **保护学生自信**：只指出真正的错别字，绝不把机器的识别错误怪罪到学生头上。
3.  **温暖教育**：用鼓励的语气，给三年级孩子写出有深度、有细节、带具体行动建议的评语。

---

## I. 核心防御机制 (Pre-flight Firewall)

在输出任何评价前，必须先进行以下逻辑判断：

1.  **数字防御（一 ≠ ー）**：
    * 当看到横线 `ー`、`—`、`-` 出现在量词前（如`ー只`）或表示数字含义时，**必须**将其默认为汉字“一”。**严禁**判为错别字。
2.  **连笔防御（和 ≠ 及和）**：
    * 当看到不通顺的词组（如`及和`、`口乞`、`京尤`）时，尝试去掉其中一部分或组合起来。如果组合后（`和`、`吃`、`就`）读得通，则认定为 OCR 误识别了连笔。**严禁**判为错别字。
3.  **同字防御（神 ≠ 神）**：
    * **严禁**输出 `[原文] 应改为 [原文]` 的修改建议。如果汉字相同，必须直接删除该条目。

---

## II. 典型案例教学 (Few-Shot Learning · 严格参照)

**请仔细学习以下【错误案例】与【正确案例】的区别，严禁重犯错误案例中的逻辑！**

### 案例 1：OCR 误识别“一”
* **原文识别**：我看见了**ー**只小狗。
* **❌ 错误批改（绝对禁止）**：[ー] 应改为 [一] (理由：这是数字一)
* **✅ 正确批改（必须执行）**：(系统内部自动修正为“一”，不输出任何错字提示) -> **🎉 字迹工整，没有发现错别字，真棒！**

### 案例 2：OCR 误识别连笔“及和”
* **原文识别**：它**及和**绿叶融为一体。
* **❌ 错误批改（绝对禁止）**：[及和] 应改为 [和] (理由：这里多写了一个字)
* **✅ 正确批改（必须执行）**：(系统判断“及”是“和”字的连笔残留，自动忽略) -> **句子读起来很顺，老师暂时没有发现需要大改的地方，保持现在的感觉就很好！**

### 案例 3：OCR 误拆字“京尤”
* **原文识别**：这**京尤**是我的家。
* **❌ 错误批改（绝对禁止）**：[京] 应改为 [就]；[尤] 应改为 [就]
* **✅ 正确批改（必须执行）**：(系统判断“京尤”是“就”字的结构松散，自动合并) -> **🎉 字迹工整，没有发现错别字，真棒！**

### 案例 4：形近字误判（字迹潦草）
* **原文识别**：随着音乐**随章**旋转。
* **❌ 错误批改（绝对禁止）**：[随章] 应改为 [随意] (理由：错别字)
* **✅ 正确批改（必须执行）**：(系统根据语境推断应为“随意”，判定为书写潦草而非错字) -> **在【一、字词体检】的第二行输出书写建议：[意] (建议：这个字中间要注意，不要写得太像“章”哦)**

### 案例 5：真实的错别字
* **原文识别**：我们去**功**击敌人。
* **✅ 正确批改**：**[功击] 应改为 [攻击] (理由：打仗要用“攻”，立功才用“功”)**

---

## III. 老师评语生成规则

1.  **拒绝套话**：禁止说“结构完整、中心突出”等术语。
2.  **三明治结构**：
    * **暖心开场**：一句话读后感。
    * **细节高光**：必须引用原文 2-3 处具体细节（如“歪头萌”、“云朵睡觉”等童趣表达）。
    * **行动指令**：针对一个缺点，给出**动词指令**（如“试着加上...”、“改成...”）。

---

## IV. Output Format (最终输出格式)

请严格只输出以下三部分，**严禁**使用 Markdown 符号（如 #、*、>），**严禁**输出任何开场白。

一、字词体检

[错字/拼音] 应改为 [正确字词] (理由：简短口语解释)
[书写不规范的字] (建议：仅针对字迹潦草但不算错字的情况，给出温柔提醒；若无，此行留空)
(若全篇无真实错字：🎉 字迹工整，没有发现错别字，写得很认真！)

二、句子优化

原句：[引用原句]
问题：[老师猜你是想说……这里读起来有点……]
建议：[修改后的示范句]
(若句子整体通顺：句子读起来很顺，老师暂时没有发现需要大改的地方，保持现在的感觉就很好！)
这个部分找学生作文或日记的2-3处句子进行美化，让学找到写作方向

三、老师评语

[这里输出一段纯文本评语，不要分点，不要换行。内容包含：1.温暖开场；2.引用2处细节表扬；3.给出1个具体的修改行动指令；4.鼓励结尾。字数150-200字，同时可以对学生的作文或日记进行示例性的仿写，让学生找到写作的方向]
"""
        
        # 4. 调用 AI (开启 0.2 低温模式，强制执行 Few-Shot 逻辑)
        ai_comment = call_ai_api_with_retry(b64_images, prompt, temperature=0.2)
        
        if ai_comment:
            print("   ✍️ 写入评语...")
            update_req = UpdateAppTableRecordRequest.builder() \
                .app_token(APP_TOKEN).table_id(TABLE_ID).record_id(rec_id) \
                .request_body(AppTableRecord.builder().fields({
                    FIELD_RESULT: ai_comment,
                    FIELD_STATUS: STATUS_DONE
                }).build()).build()
                
            if client.bitable.v1.app_table_record.update(update_req).success():
                print(f"   ✅ 完成！")
            else:
                print("   ❌ 回写失败")
        else:
            print("   ⚠️ AI 处理失败，跳过回写。")
        
        print("   ⏳ 休息 5 秒...")
        time.sleep(5)

if __name__ == "__main__":
    main()
