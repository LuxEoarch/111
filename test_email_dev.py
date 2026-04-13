import sys
sys.path.insert(0, r'd:\OpinionMonitor')
import email_service

print("测试开始：正在手动触发一次邮件告警测试...")
success = email_service.send_alert_email(
    to_email='2426175130@qq.com',
    threshold=0.9,
    article_title='论文预警系统独立测试项 - 发信引擎诊断模式',
    article_score=0.12,
    summary='这是一条由开发者手动注入的高级测试用例，用以验证 SMTP 协议授权与底层的自动化通报闭环是否顺畅。如果收到此邮件，说明系统功能正常跑通！'
)

if success:
    print("测试诊断完成：邮件发送接口返回成功！请去邮箱查收。")
else:
    print("测试诊断完成：失败。请检查上面的报错信息。")
