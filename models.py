# models.py
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

# 初始化数据库对象
db = SQLAlchemy()


from flask_login import UserMixin

# 1. 用户表 (用于管理员登录)
class User(UserMixin, db.Model):
    __tablename__ = 'user'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)  # 主键ID
    username = db.Column(db.String(50), nullable=False, unique=True)  # 用户名
    password = db.Column(db.String(100), nullable=False)  # 密码
    role = db.Column(db.String(20), default='user')  # 角色: user/admin


# 2. 舆情数据表 (存储爬虫抓取的数据)
class OpinionData(db.Model):
    __tablename__ = 'opinion_data'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    source_platform = db.Column(db.String(20))  # 来源平台 (如: 微博, 汽车之家)
    keyword = db.Column(db.String(50))  # 监控关键词 (如: 小米SU7)
    title = db.Column(db.String(200))  # 标题/微博摘要
    content = db.Column(db.Text)  # 正文内容
    summary = db.Column(db.String(500)) # 自动摘要
    # 实体识别结果 (JSON 字符串存储: {'nr':['雷军'], 'ns':['北京'], 'nt':['小米']})
    entities = db.Column(db.Text) 
    publish_time = db.Column(db.String(50))  # 发布时间
    sentiment_score = db.Column(db.Float)  # 情感分数 (0-1之间)
    keywords = db.Column(db.String(200)) # 提取的关键词 (存储为逗号分隔字符串)
    create_time = db.Column(db.DateTime, default=datetime.now)  # 入库时间


# 3. 监控任务表 (控制爬虫爬什么)
class MonitorTask(db.Model):
    __tablename__ = 'monitor_task'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    keyword = db.Column(db.String(50), nullable=False)  # 关键词
    platform = db.Column(db.String(20))  # 目标平台
    status = db.Column(db.Integer, default=1)  # 状态: 1启用, 0停止