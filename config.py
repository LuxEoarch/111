# config.py
import os

# 获取当前文件的绝对路径
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# 定义数据库文件的存储路径
SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(BASE_DIR, 'sentiment.db')
SQLALCHEMY_TRACK_MODIFICATIONS = False
SECRET_KEY = 'my_secret_key'  # 用于加密会话

# --- 发件人邮箱配置 ---
SMTP_SERVER = 'smtp.qq.com'
SMTP_PORT = 465
SMTP_USER = '2426175130@qq.com'
SMTP_PWD = 'kxleddnzgiixebga'