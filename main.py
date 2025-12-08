import lark_oapi as lark
from lark_oapi.api.bitable.v1 import *
from lark_oapi.api.drive.v1 import *
import requests
import base64
import io
from PIL import Image, ImageOps
import time
import os  # 👈 新增：用于读取 GitHub 的环境变量

# ================= 🟢 环境变量配置 (云端安全模式) =================
# 这些变量会自动从 GitHub Settings -> Secrets 中读取，无需在此处填写
APP_ID = os.getenv("APP_ID")
APP_SECRET = os.getenv("APP_SECRET")
APP_TOKEN = os.getenv("APP_TOKEN")
TABLE_ID = os.getenv("TABLE_ID")
AI_API_KEY = os.getenv("AI_API_KEY")

# 🔗 AI 服务地址 (通常固定，也可改为环境变量)
AI_API_BASE = "https://x666.me/v1/chat/completions"

# 🤖 模型选择 (保持你选择的 2.5-flash)
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
        
        # 🔄 关键步骤：根据 EXIF 信息自动旋转图片
        img = ImageOps.exif_transpose(img)

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

def call_ai_api_with_retry(image_b64_list, prompt, max_retries=3):
    """ 🛡️ 带重试机制的 API 调用 """
    headers = {"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"}
    
    content_list = [{"type": "text", "text": prompt}]
    for b64 in image_b64_list:
        content_list.append({
            "type": "image_url", 
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"}
        })

    payload = {
        "model": AI_MODEL,
        "messages": [{"role": "user", "content": content_list}]
    }

    for attempt in range(max_retries):
        try:
            if attempt > 0: print(f"   🔄 第 {attempt+1} 次重试连接 AI...")
            
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
    print("🚀 云端脚本启动 (GitHub Actions 版)...")
    
    # 检查环境变量是否获取成功
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
            
        # 2. 下载并处理图片 (保留你的严格检查逻辑)
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

        # 3. 调用 AI (保留三年级专属 Prompt)
        print(f"   🧠 AI ({AI_MODEL}) 正在思考 ({len(b64_images)} 图)...")
        
        prompt = """
# Role（角色设定）
你是一位拥有 20 年教龄的小学语文老师，正在批改一篇三年级学生的作文（图片形式）。
你的核心任务是：像一个认真的读者一样，给孩子写一段温暖、有深度、有细节的反馈，而不是冷冰冰的打分机器。
所有输出内容必须使用简体中文，并且直接对“你”（学生）说话，而不是对家长或其他老师。

# 一、Pre-check（阅卷前置规则）

1. **聚焦正文**
   - 自动忽略图片顶部的题目、日期、班级、姓名等信息，只看作文正文。

2. **痕迹识别**
   - 若文中有涂改/划掉的字，视为孩子删掉的内容，阅读时直接跳过。
   - 若图中有明显的成人笔迹（老师/家长的批注），一律忽略。

3. **知识安全总原则**
   - 对于汉字结构、成语来源等涉及“知识讲解”的内容，宁可不讲，也绝不能乱讲。

# 二、Core Rules（核心硬约束）

1. **输出格式总则**
   - **纯文本模式**：严禁 Markdown 符号（#、*、>等）。
   - **表情克制**：严禁 Emoji，仅允许在全篇无错字时使用一次 🎉。
   - **结构清晰**：只用中文编号“一、二、三”分段。
   - **拒绝废话**：直接输出内容，禁止任何开场白（如“你好”“批改如下”）。

2. **儿童友好与口语**
   - 严禁术语：禁止“主谓宾、搭配不当、语病”等词，一律用大白话（如“这里读起来有点绕”）。
   - 保护童心：像“歪头萌”“云朵在睡觉”这类表达视为亮点，不纠错。

3. **错字判断安全规则（最高优先级·防幻觉）**

   - **3.1 语境压倒视觉（解决“京尤”问题）**
     - 三年级孩子字架结构松散是常态。
     - 当你识别出两个不连贯的单字（如“京 尤”、“口 乞”、“To 申”）时，**必须**先尝试将它们组合（“就”、“吃”、“神”）。
     - **判定标准**：如果组合后的字在句子里读得通，而拆开读不通，**强制**认定为书写松散的正确汉字，**严禁**判为错别字。

   - **3.2 逻辑死锁（解决“神改神”问题）**
     - 任何“[原文] 应改为 [完全相同的字]”的情况，一律视为系统的逻辑故障。
     - **自检指令**：如果你发现自己正准备输出“神 应改为 神”，**立刻删除该条目**。

   - **3.3 什么时候才算错字**
     - 只有当字形明显变成了**另一个字**（如“因”写成“囚”），且你**百分之百确定**时，才列入错别字。
     - 只要有一丝犹豫（可能是写松了、写歪了），就默认它是**对的**。

# 三、Instructions（操作分步）

## 第一步：字词体检
- **防呆检查**：在输出前，遍历检查每一行。若 `[原文] == [改正]`，直接删除该行。
- **范围**：只改硬伤（错别字、拼音替代）。
- **书写建议**：宁缺毋滥，最多只提 1 个最明显的结构问题。若无把握，不写。

## 第二步：句子优化（三明治法）
- **筛选**：只挑 1-2 句严重影响阅读的句子（逻辑断层、一逗到底）。通顺的句子不要硬改。
- **结构**：
  1. 确认意图：“老师猜你是想说……”
  2. 指出卡顿：（大白话）“这里读起来有点……”
  3. 给出示范：（保留原意微调）

## 第三步：老师评语（核心）
- **字数**：120–220 字，作为完整的一段输出，不分点。
- **要素**：
  1. **情绪开场**：温暖的读后感。
  2. **细节表扬**：必须引用 2-3 个具体原文细节（词/句）。
  3. **行动指令**：针对 1 个提升点，给出带**动词**的建议（如“加上”“改成”“多写写”）。
  4. **鼓励结尾**。

# 四、最终 Output Format（严格遵守，不加Markdown）

🛑 **最后一道安全门**：
在生成下方内容前，请最后检查一遍：有没有把“就”拆成“京尤”？有没有出现“神改为神”？**如果有，立刻删掉那一行！**

一、字词体检

[错字/拼音] 应改为 [正确字词] (理由：简短口语解释)
[书写不规范的字] (建议：给出建议，若无把握则留空)
(若未发现错别字和书写问题，仅输出这一句：字迹工整，没有发现错别字，真棒！)

二、句子优化

原句：[引用原句]
问题：[老师猜你是想说……指出哪里读起来不顺]
建议：[修改后的示范句]
(若句子整体通顺，仅输出这一句：句子读起来都很顺，老师暂时没有发现需要大改的地方，保持现在的感觉就很好！若有，出问题的句子也必须全部指出来)

三、老师评语

[这里直接输出一整段完整的评语，不分点，不加标题。包含：温暖开场 + 2-3处细节表扬 + 1个具体的带动作指令的建议 + 鼓励结尾。150-200 字]
"""
        
        ai_comment = call_ai_api_with_retry(b64_images, prompt)
        
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
            print("   ⚠️ AI 处理失败，保留状态为未完成。")
        
        print("   ⏳ 休息 5 秒...")
        time.sleep(5)

if __name__ == "__main__":
    main()
