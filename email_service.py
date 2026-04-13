import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

try:
    SMTP_SERVER = config.SMTP_SERVER
    SMTP_PORT = config.SMTP_PORT
    SMTP_USER = config.SMTP_USER
    SMTP_PWD = config.SMTP_PWD
except Exception as e:
    print(f"DEBUG: Error loading config.py: {e}")
    SMTP_SERVER = 'smtp.qq.com'
    SMTP_PORT = 465
    SMTP_USER = ''
    SMTP_PWD = ''

def send_alert_email(to_email, threshold, article_title, article_score, summary):
    """通过配置的 SMTP 服务发送高危降级告警"""
    smtp_server = SMTP_SERVER
    smtp_port = SMTP_PORT
    smtp_user = SMTP_USER
    smtp_pwd = SMTP_PWD
    
    if not smtp_user or not smtp_pwd:
        print("\n" + "="*50)
        print(" [预警守护进程 - 系统截获高危事件] ")
        print(f" [警告] 当前抓取到了突破底线的负面新闻！")
        print(f" 触警阈值: < {threshold} | 实际检出分数: {article_score}")
        print(f" 风险源文: {article_title}")
        print(" [通知] 由于 config.py 未配置发件授权码，此处仅作终端展示 (建议配置后直接截图邮箱)")
        print("="*50 + "\n")
        return False
    sender = smtp_user
    receivers = [to_email]
    from email.utils import formataddr
    
    html_content = f"""
    <div style="font-family: Arial, sans-serif; padding: 20px; border: 1px solid #e0e0e0; border-radius: 5px;">
        <h2 style="color: #D32F2F;">⚠️ 舆情预警系统 - 自动化紧急通报</h2>
        <p>系统后台守护进程监测到，最新抓取的全网声量数据，突破了您在看板中设置的防线。</p>
        <hr>
        <h3>📌 触发指标剖析</h3>
        <ul>
            <li><b>设定防御阈值：</b>{threshold}</li>
            <li><b>破线情感极值：</b><span style="color:red; font-weight:bold;">{article_score}</span></li>
            <li><b>污染源标题：</b>{article_title}</li>
        </ul>
        <h3>🤖 NLP 智能诊断溯源与干预建议</h3>
        <div style="background-color: #f9f9f9; padding: 15px; border-left: 4px solid #D32F2F;">
            {summary}
        </div>
    </div>
    """
    
    # 彻底简化为单一 MIMEText，绕过 QQ 邮箱对包含附件的多段传输结构的严苛拦截
    message = MIMEText(html_content, 'html', 'utf-8')
    message['From'] = formataddr(("OpinionMonitor", sender))
    message['To'] = formataddr(("Admin", to_email))
    message['Subject'] = Header(f'【舆情高危告警】新追踪到高危事件', 'utf-8')
    
    try:
        smtpObj = smtplib.SMTP_SSL(smtp_server, smtp_port)
        # 支持开启调试模式查看底层断联原因
        # smtpObj.set_debuglevel(1)
        smtpObj.login(smtp_user, smtp_pwd)
        smtpObj.sendmail(sender, receivers, message.as_string())
        smtpObj.quit()
        print(f"\n[预警守护进程] 邮件大网分发成功！目标接收靶 -> {to_email}")
        return True
    except Exception as e:
        print(f"\n[预警守护进程] 致命网络错误，电文发射端故障: {e}")
        return False
