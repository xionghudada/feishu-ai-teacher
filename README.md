📚 Feishu AI Homework Grader

An automated homework grading bot designed for primary school teachers. 

This tool integrates Feishu (Lark) Bitable with Google Gemini 2.5 Pro to automatically analyze student compositions, 

generate warm and educational feedback, and update grading records in real-time.

Running entirely on GitHub Actions, 

it requires zero server maintenance and operates on a customized schedule to match the teacher's working hours.

🚀 How It Works

Monitor: The script connects to a Feishu Bitable (Multidimensional Table) and scans for records marked as "未完成" (Unfinished).

Fetch: It retrieves the homework images uploaded by students/parents via Feishu forms.

Process:

Downloads and compresses images to optimize API usage.

Auto-rotates images based on EXIF data.

Analyze (AI): Sends the content to the Gemini 2.5 Pro model (via an OpenAI-compatible gateway) with a strictly engineered prompt designed for primary school education.

Checks for typos and pinyin errors.

Optimizes sentence structure.

Generates encouraging and specific teacher comments.

Feedback: Writes the AI-generated evaluation back into the Feishu table and updates the status to "已完成" (Done).

🛡️ Privacy & Safety

Data Security: All API keys are stored in GitHub Secrets and are not visible in the codebase.

Content Filtering: The AI prompt includes specific instructions to ignore personal information (names, dates) and focus solely on the text body.

Educational Guardrails: The AI is instructed to avoid inventing facts or explaining complex character structures incorrectly.

📝 License

This project is open-source. Feel free to fork and modify it for your own educational needs.

Disclaimer: AI-generated feedback is for reference only. Teachers should review the comments to ensure accuracy and appropriateness for their students.

