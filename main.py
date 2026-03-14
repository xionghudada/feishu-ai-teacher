import lark_oapi as lark
from lark_oapi.api.bitable.v1 import *
from lark_oapi.api.drive.v1 import *
import requests
import base64
import io
from PIL import Image, ImageOps
import time
import os
import random

# ================= 环境变量配置 =================
APP_ID = os.getenv("APP_ID")
APP_SECRET = os.getenv("APP_SECRET")
APP_TOKEN = os.getenv("APP_TOKEN")
TABLE_ID = os.getenv("TABLE_ID")
AI_API_KEY = os.getenv("AI_API_KEY")

AI_API_BASE = "https://x666.me/v1/chat/completions"
AI_MODEL = "gemini-3-flash-preview"

FIELD_IMG = "上传作文图片"
FIELD_RESULT = "评语"
FIELD_STATUS = "单选"
STATUS_TODO = "未完成"
STATUS_DONE = "已完成"
# ==========================================================

client = lark.Client.builder().app_id(APP_ID).app_secret(APP_SECRET).build()

# ================= 重试机制 =================
NON_RETRYABLE_HTTP = {400, 401, 403, 404}


class AbortRetry(Exception):
    """不可重试错误，立即终止重试"""
    pass


def with_retry(fn, *, max_retries=3, delay=3, desc="操作"):
    """通用重试。fn 返回非 None 视为成功；返回 None 触发重试；抛 AbortRetry 立即终止"""
    for attempt in range(1, max_retries + 1):
        try:
            result = fn()
            if result is not None:
                return result
        except AbortRetry as e:
            print(f"   [x] {desc}不可重试: {e}")
            return None
        except Exception as e:
            if attempt == max_retries:
                print(f"   [x] {desc}重试{max_retries}次均失败: {e}")
                return None
        if attempt < max_retries:
            print(f"   [!] {desc}第{attempt}/{max_retries}次失败，{delay}秒后重试...")
            time.sleep(delay)
    print(f"   [x] {desc}重试{max_retries}次均失败")
    return None


# ==========================================================

def compress_image(image_binary, max_side=1024, quality=60):
    """图片压缩处理：限制尺寸、自动扶正方向、转为 JPEG Base64"""
    try:
        img = Image.open(io.BytesIO(image_binary))
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
        print(f"   [x] 图片压缩出错: {e}")
        return None


def call_ai_api_with_retry(image_b64_list, prompt, max_retries=3, temperature=0.85):
    """调用 AI 接口，区分可重试(429/5xx/网络异常)与不可重试(400/401/403/404)错误"""
    headers = {"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"}
    content_list = [{"type": "text", "text": prompt}]
    for b64 in image_b64_list:
        content_list.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

    payload = {
        "model": AI_MODEL,
        "messages": [{"role": "user", "content": content_list}],
        "temperature": temperature
    }

    def _call():
        resp = requests.post(AI_API_BASE, json=payload, headers=headers, timeout=60)
        if resp.status_code in NON_RETRYABLE_HTTP:
            raise AbortRetry(f"HTTP {resp.status_code}")
        if resp.status_code != 200:
            print(f"   [!] AI 接口返回 HTTP {resp.status_code}")
            return None
        text = resp.json().get('choices', [{}])[0].get('message', {}).get('content', '').strip()
        return text or None

    return with_retry(_call, max_retries=max_retries, delay=5, desc="AI 接口调用")


def main():
    print("云端脚本启动 (V10.0 12类人格随机抽取)...")

    personas = [
        # --- 原有 5 种 ---
        {"name": "细节控老班", "rule": "禁'哇、感叹号'。首句必须从具体动作切入。用观察代替赞美。"},
        {"name": "好奇心顽童", "rule": "禁'老师、应该'。首句必须是迫不及待的提议/提问，以平辈视角交流。"},
        {"name": "诗人姐姐", "rule": "禁'逻辑、修改'。首句必须是充满画面感的比喻，用色彩和声音代替叙述。"},
        {"name": "幽默大叔", "rule": "禁'规范、严肃'。必须包含一处'自黑'或对文中趣事的调侃，语气风趣。"},
        {"name": "能量教练", "rule": "禁'可能、好像'。语气短促有力，首句必须是充满力度的肯定。"},
        # --- 新增 7 种 ---
        {"name": "故事大王", "rule": "禁'不错、很好'。首句必须把孩子写的情节顺势往下接，像听故事听到一半追问'然后呢'，用讲故事的口吻点评。"},
        {"name": "探险家船长", "rule": "禁'加油、努力'。首句必须用探险或寻宝的比喻切入，把文中亮点当作'发现的宝藏'来惊叹。"},
        {"name": "暖心学姐", "rule": "禁'但是、不过、然而'。全文不出现转折否定词，用'如果再加上……就更妙了'的句式给建议，语气温柔亲切。"},
        {"name": "侦探柯南", "rule": "禁'希望、建议'。首句必须像破案一样指出文中一个被忽略的隐藏细节，用推理口吻层层分析。"},
        {"name": "动画导演", "rule": "禁'不足、欠缺'。把作文想象成一部动画片，用分镜头、画面定格、配乐等比喻来点评。"},
        {"name": "美食评论家", "rule": "禁'优秀、棒'。用品尝美食的方式点评文章，好句子是'回味无穷的味道'，语气像在分享一道私房菜。"},
        {"name": "时光旅行者", "rule": "禁'总之、综上'。首句必须从'穿越到你写的那个场景'开始，用身临其境的第一人称描述所见所感。"},
    ]

    if not APP_ID or not AI_API_KEY:
        print("[x] 错误：环境变量缺失！")
        return

    # 1. 查找待处理记录（带重试）
    filter_cmd = f'CurrentValue.[{FIELD_STATUS}] = "{STATUS_TODO}"'

    def _list_records():
        req = ListAppTableRecordRequest.builder().app_token(APP_TOKEN).table_id(TABLE_ID).filter(filter_cmd).build()
        resp = client.bitable.v1.app_table_record.list(req)
        if not resp.success():
            print(f"   [!] 查询失败: code={resp.code}, msg={resp.msg}")
            return None
        return resp.data.items or []

    records = with_retry(_list_records, desc="查询待处理记录")
    if records is None:
        print("[x] 查询待处理记录失败，请检查配置。")
        return
    if not records:
        print("[ok] 所有作业已处理完毕。")
        return

    for i, record in enumerate(records):
        rec_id = record.record_id
        student_name = record.fields.get("学生姓名", "未知学生")
        print(f"\n[{i+1}/{len(records)}] 正在批改 {student_name}...")

        persona = random.choice(personas)
        print(f"   抽到性格：{persona['name']}")

        img_list = record.fields.get(FIELD_IMG)
        if not img_list: continue

        # 2. 下载图片（带重试）
        b64_images = []
        for img_info in img_list:
            token = img_info['file_token']

            def _download(t=token):
                resp = client.drive.v1.media.download(DownloadMediaRequest.builder().file_token(t).build())
                if not resp.success():
                    print(f"   [!] 图片下载失败: code={resp.code}, msg={resp.msg}")
                    return None
                return resp.file.read()

            raw = with_retry(_download, desc=f"下载图片({token[:8]}...)")
            if raw:
                b64 = compress_image(raw)
                if b64: b64_images.append(b64)

        if not b64_images: continue

        # 3. 构建 Prompt
        prompt = f"""
# Role
你是一位资深小学语文老师。当前性格人设：【{persona['name']}】。
专属规则：{persona['rule']}。

# 一、阅卷前置规则
1. **完全忽略错别字**：自动修正理解，严禁提及"错字、字迹、书写"。
2. **包容创新表达**：如学生使用"呆头萌"、"绝绝子"等网络词汇，请将其视为"灵动闪光点"给予表扬。
3. **术语脱敏**：禁止使用"主谓宾、拟人"等术语，改用大白话。
4. **模版清除**：禁止出现"你的作文"、"读完你的文章"、"老师发现"等AI感极强的套话。

# 二、核心逻辑
1. **第一部分（点评）**：严格遵守【{persona['name']}】的首句要求。直接切入情节，引用细节。
2. **第二部分（诊所）**：找1处优化点，用"老师悄悄话"给出动作性建议。
3. **第三部分（魔法变身）**：重写为三年级满分范文，保留并升华孩子原有的灵动词汇。

# 三、Output Format（严格遵守，不加Markdown）

一、老师评语
[根据选定性格输出 150-200 字。首句直接切入情节。]

二、句子诊所
1. 原句：[引用原文，自动修正错别字]
   老师悄悄话：[大白话建议]
   试着改成：[示范]

三、魔法变身
[重写后的完整短文。]
"""

        # 4. 调用 AI（已内置重试）
        ai_comment = call_ai_api_with_retry(b64_images, prompt)

        if ai_comment:
            # 5. 回写飞书（带重试）
            def _update():
                update_req = UpdateAppTableRecordRequest.builder() \
                    .app_token(APP_TOKEN).table_id(TABLE_ID).record_id(rec_id) \
                    .request_body(AppTableRecord.builder().fields({FIELD_RESULT: ai_comment, FIELD_STATUS: STATUS_DONE}).build()).build()
                resp = client.bitable.v1.app_table_record.update(update_req)
                if not resp.success():
                    print(f"   [!] 回写失败: code={resp.code}, msg={resp.msg}")
                    return None
                return True

            if with_retry(_update, desc=f"回写{student_name}结果"):
                print(f"   [ok] {student_name} 完成！")
            else:
                print(f"   [x] {student_name} AI批改成功但回写飞书失败，请手动处理")

        time.sleep(2)


if __name__ == "__main__":
    main()
