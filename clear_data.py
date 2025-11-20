import lark_oapi as lark
from lark_oapi.api.bitable.v1 import *
import os
import time

# ================= 🟢 环境变量配置 =================
# 直接读取你已经设置好的 Secrets，不需要重新配置
APP_ID = os.getenv("APP_ID")
APP_SECRET = os.getenv("APP_SECRET")
APP_TOKEN = os.getenv("APP_TOKEN")
TABLE_ID = os.getenv("TABLE_ID")
# ===============================================

# 初始化飞书客户端
client = lark.Client.builder().app_id(APP_ID).app_secret(APP_SECRET).build()

def main():
    print("🗑️ 启动云端清空程序...")
    
    # 1. 获取所有记录 ID
    req = ListAppTableRecordRequest.builder() \
        .app_token(APP_TOKEN).table_id(TABLE_ID) \
        .page_size(100) \
        .build()
        
    resp = client.bitable.v1.app_table_record.list(req)
    
    if not resp.success():
        print(f"❌ 读取表格失败: {resp.msg}")
        return

    records = resp.data.items
    if not records:
        print("✅ 表格已经是空的，无需清理。")
        return

    print(f"📋 发现 {len(records)} 条数据，准备全部删除...")

    # 2. 提取 ID 列表
    record_ids = [r.record_id for r in records]
    
    # 3. 批量删除 (飞书 API 每次最多删 100 条)
    # 即使你每天只有 52 条，这个逻辑也能保证未来扩容时的稳定性
    batch_size = 100
    for i in range(0, len(record_ids), batch_size):
        batch_ids = record_ids[i : i + batch_size]
        
        del_req = BatchDeleteAppTableRecordRequest.builder() \
            .app_token(APP_TOKEN).table_id(TABLE_ID) \
            .request_body(BatchDeleteAppTableRecordRequestBody.builder()
                .records(batch_ids)
                .build()) \
            .build()
            
        del_resp = client.bitable.v1.app_table_record.batch_delete(del_req)
        
        if del_resp.success():
            print(f"   🗑️ 已删除 {len(batch_ids)} 条记录...")
        else:
            print(f"   ❌ 删除失败: {del_resp.msg}")
        
        time.sleep(1) # 防止接口太快

    print("🎉 表格清空完成！空间已释放，准备迎接明天的新作业。")

if __name__ == "__main__":
    main()
