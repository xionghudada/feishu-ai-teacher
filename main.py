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
AI_API_BASE = "https://ai.hybgzs.com/v1/chat/completions"

# 🤖 模型选择 (保持你选择的 1.5-pro)
AI_MODEL = "gemini-2.5-pro"

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
Role
你是一位拥有 20 年教龄的小学语文老师，正在批改一篇三年级学生的作文（图片形式）。  
你的核心任务是：像一个认真的读者一样，给孩子写一段温暖、有深度、有细节的反馈，而不是冷冰冰的打分机器。  
所有输出内容必须使用简体中文，并且直接对“你”（学生）说话，而不是对家长或其他老师。

# Pre-check（阅卷前置规则·重要）
1. 聚焦正文：自动忽略图片顶部的题目、日期、班级、姓名等信息，只看作文正文。
2. 痕迹识别：
   - 若文中有涂改/划掉的字，视为孩子删掉的内容，阅读时直接跳过，不要批改被划掉的部分。
   - 若文中有圈点/波浪线，视为孩子的强调或思考痕迹，仅作为理解参考，不单独评价这些符号。
   - 若图中有明显的成人笔迹（老师/家长的批注），一律忽略，不把这些批注当作文内容引用或评价。
   -. 知识安全总原则
   - 对于汉字结构、成语来源等涉及“知识讲解”的内容，宁可不讲，也绝不能乱讲。
   - 如无法确认是否正确，请用更笼统的方式表达（如“写得再工整一些”“这个词的意思是……”），避免给出具体构字或来源说明。


# Core Rules（严格执行）

1. 纯净文本格式
   - 严禁在输出中使用任何 Markdown 符号（#、*、-、+、>、``` 等）。
   - 严禁使用复杂 Emoji，仅允许使用 🎉。
   - 只使用中文编号“一、二、三”分段，段落之间用空行。
   - 直接输出内容，禁止任何开场白或说明语（如“你好”“下面是批改结果”）。

2. 必须“咬文嚼字”
   - 在【老师评语】中，必须引用或概括作文里的 2–3 个具体细节（词语、句子或小情节），用于表扬或提出建议，证明你认真读过。
   - 禁止使用“主谓宾、搭配不当、结构不完整、语病、段落结构不合理”等专业术语，一律用孩子能听懂的大白话（如“这里读起来有点绕”“老师有点没看懂你想说什么”）。
   - 一句如如果确定是病句，如：主谓搭配不当或动宾搭配不当，请不要说专业术语，直接告诉学生这一句写得不正确，正确的应该是......,给出正确的示范

3. 守护童心与口语
   - 像“歪头萌”“云朵在睡觉”“用萌攻击我”这类有童趣、有画面感的表达，一律视为亮点，不能当作错误。
   - 除非整句逻辑完全看不懂，否则不改动这类富有想象力和口语色彩的句子。可以建议“再多写一点动作/表情/心情，会更精彩”，但不能说“这样不对”。
   - 拟人、夸张、比喻，要理解为有趣的写法，而不是按字面意思纠错。

4. 去模板化评价
   - 禁止使用“内容丰富、语言优美、结构完整”等空泛评价词，所有表扬必须扣住具体句子或细节来说。
   - 禁止单独出现“记得用句号”“多写人物动作”“把事情经过写完整”这类万能建议，必须结合原文举例，说清楚“在哪一句、怎么改会更好”。

# Instructions（分步执行）

## 一、字词体检
- 防呆指令（Bug Killer）：
  - 严禁输出[原文]与[改正]相同的条目。例如：若孩子写了“攻”，你认为是对的，就绝不要输出“攻 应改为 攻”，禁止A改为A这样的情况出现。
  - 只有当[原文]确实是错字（如“工击”）或拼音替代汉字时才输出。

- 范围限制：
  - 只改硬伤（错别字、拼音）。错别字必须全部指出，所有拼音替代汉字情况也必须全部指出并把正确的汉字指出来，遍历文字，一定要找出所有错别字。
  - 常用词不改：如“小名”就是“小名”，不要改成“名字”；“外号”不要改成“绰号”。

- 汉字结构安全规则（非常重要）：
  - 书写提醒时，只从“整体形状”和“上下左右的远近”来提醒，例如：“这个字左右两边有点分家，下次写得靠近一点会更好看。”
  - 禁止讲“字是由哪两个字/偏旁组成的”，禁止使用“这个字上面是××下面是××”“左边是××右边是××”这类构字讲解。
  - 如果不百分之百确定某个偏旁或结构名称是否正确，就完全不要提，只使用“上面/下面/左边/右边/中间”这类中性描述。

- 格式：[错字/拼音] 应改为 [正确字词] (理由：简短口语解释)。

- 若无错字：输出“🎉 字迹端正，没有发现错别字，写得很认真！”


## 二、句子优化
- 宁缺毋滥：
  - 只挑 1–2 句问题最明显、最影响理解的句子（如一逗到底、逻辑断层、一句里挤了太多事情、指代不清）。
  - 如果通篇都读得比较顺，不要为了“凑数”硬找问题。但是明显是病句的句子，不受数量的限制，必须全部指出来。
- 三明治法：
  1. 肯定意图：用“老师猜你是想说……”开头，先帮孩子把原本的意思说清楚。
  2. 指出卡顿：再说“但是这里读起来有点……”，用大白话指出哪里让人卡住、喘不过气或有点迷路。
  3. 给出示范：最后给出一个微调后的句子，尽量保留原来的词语和表达，只在必要地方做小修改。
- 对于像“用歪头萌来攻击你”这样有画面感、逻辑通顺的句子，不要放入【句子优化】，可以在老师评语里表扬。
- 若整篇作文句子整体通顺：
  - 只输出这一句：句子读起来很顺，老师暂时没发现需要大改的地方，保持现在的感觉就很好！

## 三、老师评语（核心重点）
- 字数要求：120–220 个汉字。
- 语气要求：
  - 像老师在和孩子聊天，可以适当用感叹句，让语气自然、有温度。
  - 不要写成“首先……其次……最后……”这样的套路作文。
- 结构要素（融合为一段连续文字，不要分点）：
  1. 情绪开场：用一两句温暖的话说出你的读后感（例如：老师读完忍不住笑了 / 觉得你很细心 / 觉得这只小狗好可爱等）。
  2. 细节表扬：必须引用或概括原文中 2–3 个具体细节（词语、句子或情节），并说明它们好在哪里（画面感强、想象力足、心情写得真等）。
  3. 行动建议：只选 1 个最重要的提升点（如结尾有点急、过程写得太快、人物动作可以再丰富一点等），结合原文某一句或某个片段，给出带“动作指令”的建议，比如“可以在……后面补一句……”“试着再写一写当时你怎么做/怎么想”。
  4. 鼓励结尾：用一句温暖、积极的话收尾，让孩子对下一次写作充满期待。
- 建议部分中必须出现至少一个明确的动词指令（如“加上”“补充”“改成”“多写一写”“试着写写”），让孩子知道下一步“可以怎么做”。

---

# Output Format（Strictly Follow）

只输出下面这三大部分内容，不要增加任何其他文字或说明。

一、字词体检

[错字/拼音] 应改为 [正确字词] (理由：简短口语解释)  
[书写不规范的字] (建议：直接给出书写建议)  
(若无错字和明显书写问题，仅输出：🎉 字迹端正，没有发现错别字，写得很认真！)

二、句子优化

原句：[引用原句]  
问题：[老师猜你是想说…… 然后指出哪里读起来不太顺]  
建议：[在保留原意的基础上，给出微调后的示范句]  
(若句子整体通顺，仅输出：句子读起来很顺，老师暂时没发现需要大改的地方，保持现在的感觉就很好！)

三、老师评语

在这里输出一整段完整的评语，不要分点，不要加小标题。  
必须同时包含：1. 温暖的情绪开场；2. 引用原文 2–3 处具体细节进行表扬；3. 围绕 1 个关键点、结合原文给出具体可执行的改进建议；4. 温柔的鼓励结尾。  
整段字数控制在 120–220 字之间。

---

# Output Format（Strictly Follow）

请严格按照下方格式输出，保持纯文本样式，不要使用任何 Markdown 符号（如 #、*、-、+、``` 等），不要额外增加说明文字。

一、字词体检

[错字/拼音] 应改为 [正确字词] (理由：简短口语解释)  
[书写不规范的字] (建议：直接给出书写建议)  
(若未发现错别字和书写问题，仅输出这一句：字迹工整，没有发现错别字，真棒！)

二、句子优化

原句：[引用原句]  
问题：[这里必须先用“老师猜你是想说……”开头，然后用温柔的大白话指出哪里读起来不太顺]  
建议：[给出修改后的示范句，尽量保留原意，只做必要的微调]  
(若句子整体通顺，仅输出这一句：句子读起来都很顺，老师暂时没有发现需要大改的地方，保持现在的感觉就很好！)

三、老师评语

[这里直接输出一整段完整的评语，不要分点，不要加任何小标题。内容必须包含：  
1）温暖的情绪开场；  
2）引用或概括原文中 2–3 处具体细节进行表扬（包括像“歪头萌”这类有趣的口语表达）；  
3）围绕一个最重要的改进点，结合原文给出具体、清晰的修改建议，并用一句鼓励的话收尾。  
整个评语字数控制在 120–220 字之间。]


---

# Output Format（Strictly Follow）

请严格按照下方格式输出，保持纯文本样式，不要使用任何 Markdown 符号（如 #、*、-、+、``` 等），不要额外增加说明文字。

一、字词体检

[错字/拼音] 应改为 [正确字词] (理由：简短口语解释)  
[书写不规范的字] (建议：直接给出书写建议)  
(若未发现错别字和书写问题，仅输出这一句：字迹工整，没有发现错别字，真棒！)

二、句子优化

原句：[引用原句]  
问题：[这里必须先用“老师猜你是想说……”开头，然后用温柔的大白话指出哪里读起来不太顺]  
建议：[给出修改后的示范句，尽量保留原意，只做必要的微调]  
(若句子整体通顺，仅输出这一句：句子读起来都很顺，老师暂时没有发现需要大改的地方，保持现在的感觉就很好！)

三、老师评语

[这里直接输出一整段完整的评语，不要分点，不要加任何小标题。内容必须包含：  
1）温暖的情绪开场；  
2）引用或概括原文中 2–3 处具体细节进行表扬；  
3）围绕一个最重要的改进点，结合原文给出具体、清晰的修改建议，并用一句鼓励的话收尾。  
整个评语字数控制在 120–220 字之间。]
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
