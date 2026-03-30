# app.py
from flask import Flask, render_template, jsonify, request, redirect, url_for, flash, send_file, make_response
from config import SQLALCHEMY_DATABASE_URI, SQLALCHEMY_TRACK_MODIFICATIONS, SECRET_KEY
from models import db, User, MonitorTask, OpinionData
from datetime import datetime, timedelta
import collections
from flask_apscheduler import APScheduler
from sqlalchemy import func, case, desc
import jieba
import jieba.posseg as pseg
import json
from sklearn.feature_extraction.text import CountVectorizer, TfidfVectorizer
from sklearn.decomposition import LatentDirichletAllocation
import os

# 加载自定义分词词典 (确保专有名词在聚类时不被打碎)
if os.path.exists("user_dict.txt"):
    jieba.load_userdict("user_dict.txt")
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
import pandas as pd
from io import BytesIO
from xhtml2pdf import pisa
from functools import wraps

app = Flask(__name__)

# 读取配置
app.config['SQLALCHEMY_DATABASE_URI'] = SQLALCHEMY_DATABASE_URI
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = SQLALCHEMY_TRACK_MODIFICATIONS
app.config['SECRET_KEY'] = SECRET_KEY
# 开启调度器 API
app.config['SCHEDULER_API_ENABLED'] = True 

# 绑定数据库
db.init_app(app)

# 初始化调度器
scheduler = APScheduler()
scheduler.init_app(app)

# 初始化 Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login' 

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- RBAC 权限控制装饰器 ---
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            flash('您没有权限执行此操作 (需要管理员权限)', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def scheduled_spider_task():
    """定时任务：每5分钟执行一次爬虫"""
    print(f"\n>>> [Scheduler] 定时爬虫任务启动: {datetime.now()}")
    try:
        # 延迟导入 spider，避免循环引用
        import spider
        spider.run_spider()
        print(f">>> [Scheduler] 定时爬虫任务完成: {datetime.now()}\n")
    except Exception as e:
        print(f"!!! [Scheduler] 任务执行失败: {e}")

# --- 认证路由 ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and user.password == password: 
            login_user(user)
            return redirect(url_for('index'))
        else:
            flash('用户名或密码错误')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        # 简单的后门：如果用户名包含 'admin'，则自动设为管理员 (仅用于演示)
        role = 'admin' if 'admin' in username else 'user'
        
        if User.query.filter_by(username=username).first():
            flash('用户名已存在')
        else:
            new_user = User(username=username, password=password, role=role)
            db.session.add(new_user)
            db.session.commit()
            flash(f'注册成功，角色: {role}，请登录')
            return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# --- 核心页面 (需登录) ---
@app.route('/')
@login_required 
def index():
    return render_template('index.html', user=current_user)

@app.route('/data')
@login_required
def data_list():
    page = request.args.get('page', 1, type=int)
    keyword = request.args.get('keyword', '', type=str)
    query = OpinionData.query
    if keyword:
        query = query.filter(OpinionData.title.contains(keyword))
    pagination = query.order_by(desc(OpinionData.id)).paginate(page=page, per_page=15, error_out=False)
    return render_template('data_list.html', user=current_user, pagination=pagination, keyword=keyword)

@app.route('/report')
@login_required
def report_page():
    return render_template('report.html', user=current_user)

# --- 导出功能 ---
@app.route('/api/export/excel')
@login_required
def export_excel():
    try:
        all_data = OpinionData.query.all()
        data_list = []
        for d in all_data:
            data_list.append({
                'ID': d.id,
                '标题': d.title,
                '来源': d.source_platform,
                '发布时间': d.publish_time,
                '情感分': d.sentiment_score,
                '关键词': d.keywords,
                '摘要': d.summary,
                '正文': d.content[:500] + '...' 
            })
        df = pd.DataFrame(data_list)
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='舆情数据')
        output.seek(0)
        filename = f"opinion_data_{datetime.now().strftime('%Y%m%d%H%M')}.xlsx"
        return send_file(output, download_name=filename, as_attachment=True)
    except Exception as e:
        print(f"Export Error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/export/pdf')
@admin_required  # 只有管理员可以导出 PDF 报告
def export_pdf():
    """导出 PDF 分析报告"""
    start_date = request.args.get('start', '')
    end_date = request.args.get('end', '')
    
    try:
        query = OpinionData.query
        date_range_str = "全库历史数据"
        
        if start_date and end_date:
            try:
                end_dt = (datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
                query = query.filter(OpinionData.publish_time >= start_date, OpinionData.publish_time <= end_dt)
                date_range_str = f"{start_date} 至 {end_date}"
            except Exception:
                pass
                
        # 1. 获取近期数据用于排版
        recent_data = query.order_by(desc(OpinionData.id)).limit(20).all()
        
        # 2. 核心大盘指标运算
        total_num = query.count()
        neg_num = query.filter(OpinionData.sentiment_score <= 0.4).count()
        pos_num = query.filter(OpinionData.sentiment_score >= 0.6).count()
        
        # 智能判定大盘情绪
        mood = "偏正面" if pos_num > neg_num else "偏负面" if neg_num > pos_num else "中性平衡"
        ratio = round((neg_num / total_num * 100), 1) if total_num > 0 else 0
        
        # 3. 提取核心聚焦点用于自然语言生成
        kws_data = query.with_entities(OpinionData.keywords).filter(OpinionData.keywords != None).order_by(desc(OpinionData.id)).limit(500).all()
        all_kws = []
        for row in kws_data:
            if row[0]:
                for k in row[0].replace('，', ',').split(','):
                    k = k.strip()
                    if len(k) >= 2 and not k.isdigit() and k not in ["用户", "模型", "发展", "市场", "公司", "企业", "表示", "相关", "发现", "工作", "问题", "技术", "服务", "进行"]:
                        all_kws.append(k)
        top_kws = [k[0] for k in collections.Counter(all_kws).most_common(5)]
        
        # --- [重点创新] NLP 自动生成报告总结文字 ---
        kws_str = "、".join(top_kws) if top_kws else "暂无"
        ai_summary = f"【系统诊断结论】分析时段 ({date_range_str}) 内，舆情监控系统共提取全网样本 {total_num} 条。全网整体舆论盘面情绪基调为【{mood}】（负面舆情 {neg_num} 条，占比约为 {ratio}%）。根据 NLP 知识图谱引擎追踪，该周期的舆论漩涡的绝对中心高度聚焦于【{kws_str}】等议题。建议有关部门定点观察与正向引导。"

        stats = {
            'total': total_num,
            'neg': neg_num,
            'pos': pos_num,
            'ratio': ratio,
            'time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'date_range': date_range_str,
            'top_kws': top_kws,
            'ai_summary': ai_summary
        }
        
        # 4. 渲染专用的 PDF HTML 模板
        # (xhtml2pdf 对复杂 css 支持有限，需使用简单内联样式)
        html_content = render_template('report_pdf.html', data=recent_data, stats=stats)
        
        # 3. 生成 PDF
        output = BytesIO()
        pisa_status = pisa.CreatePDF(html_content, dest=output, encoding='utf-8')
        
        if pisa_status.err:
            return jsonify({'error': 'PDF generation failed'}), 500
            
        output.seek(0)
        filename = f"report_{datetime.now().strftime('%Y%m%d')}.pdf"
        
        response = make_response(output.getvalue())
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'attachment; filename={filename}'
        return response
        
    except Exception as e:
        print(f"PDF Export Error: {e}")
        return jsonify({'error': str(e)}), 500

# --- API 接口 (需登录) ---
@app.route('/api/stats')
@login_required
def get_stats():
    start_date = request.args.get('start', '')
    end_date = request.args.get('end', '')
    
    filters = []
    if start_date and end_date:
        try:
            end_dt = (datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
            filters.append(OpinionData.publish_time >= start_date)
            filters.append(OpinionData.publish_time <= end_dt)
        except Exception:
            pass

    total_count = db.session.query(func.count(OpinionData.id)).filter(*filters).scalar() or 0
    
    sentiment_stats = db.session.query(
        func.sum(case((OpinionData.sentiment_score >= 0.6, 1), else_=0)), # Positive
        func.sum(case((OpinionData.sentiment_score <= 0.4, 1), else_=0)), # Negative
        func.sum(case(((OpinionData.sentiment_score > 0.4) & (OpinionData.sentiment_score < 0.6), 1), else_=0)), # Neutral
        func.avg(OpinionData.sentiment_score)
    ).filter(*filters).first()
    
    pos_count = sentiment_stats[0] or 0
    neg_count = sentiment_stats[1] or 0
    neu_count = sentiment_stats[2] or 0
    avg_score = sentiment_stats[3] or 0.5
    today_str = datetime.now().strftime('%Y-%m-%d')
    today_count = db.session.query(func.count(OpinionData.id)).filter(
        OpinionData.publish_time.like(f"{today_str}%")
    ).scalar() or 0
    
    # 定义热词专属过滤黑名单 (剔除泛泛而谈的词汇)
    HOTWORD_BLOCKLIST = {
        "用户", "模型", "企业", "发展", "市场", "公司", "行业", "产品", "品牌", "业务", "平台",
        "表示", "认为", "指出", "相关", "工作", "提供", "支持", "问题", "技术", "服务", "进行",
        "记者", "编辑", "文章", "发布", "数据", "核心", "能力", "目前", "智能", "系统", "功能", "实现",
        "时代", "领域", "带来", "创新", "应用"
    }

    keyword_data = db.session.query(OpinionData.keywords).filter(OpinionData.keywords != None).filter(*filters).all()
    keyword_list = []
    for item in keyword_data:
        if item.keywords:
             kws = item.keywords.replace('，', ',').split(',')
             for k in kws:
                 kw = k.strip()
                 if kw and not kw.isdigit() and len(kw) >= 2 and kw not in HOTWORD_BLOCKLIST:
                     keyword_list.append(kw)
                     
    top_keywords = collections.Counter(keyword_list).most_common(10)
    recent_data = db.session.query(
        OpinionData.title, 
        OpinionData.source_platform, 
        OpinionData.publish_time, 
        OpinionData.sentiment_score,
        OpinionData.summary
    ).filter(*filters).order_by(OpinionData.id.desc()).limit(10).all()
    recent_list = [{
        'title': d.title,
        'source': d.source_platform,
        'time': d.publish_time,
        'score': round(d.sentiment_score, 2) if d.sentiment_score else 0.5,
        'summary': d.summary if d.summary else "暂无摘要"
    } for d in recent_data]
    
    # --- [新增] 生成供给前端直显的 AI 总结 ---
    mood = "偏正面" if pos_count > neg_count else "偏负面" if neg_count > pos_count else "中性平衡"
    ratio = round((neg_count / total_count * 100), 1) if total_count > 0 else 0
    date_range_str = f"{start_date} 至 {end_date}" if (start_date and end_date) else "全库历史数据"
    kws_str = "、".join([k for k, v in top_keywords[:5]]) if top_keywords else "暂无"
    
    ai_summary = f"【系统诊断结论】分析时段 ({date_range_str}) 内，舆情监控系统共提取全网样本 {total_count} 条。全网整体舆论盘面情绪基调为【{mood}】（负面舆情 {neg_count} 条，占比约为 {ratio}%）。根据 NLP 知识图谱引擎追踪，该周期的舆论漩涡的绝对中心高度聚焦于【{kws_str}】等议题。建议有关部门定点观察与正向引导。"

    return jsonify({
        'total': total_count,
        'today_new': today_count,
        'negative_warning': neg_count,
        'avg_sentiment': round(avg_score, 2),
        'sentiment_pie': [
            {'value': pos_count, 'name': '正面'},
            {'value': neu_count, 'name': '中性'},
            {'value': neg_count, 'name': '负面'}
        ],
        'top_keywords': [{'name': k, 'value': v} for k, v in top_keywords],
        'recent_list': recent_list,
        'ai_summary': ai_summary
    })

@app.route('/api/trend')
@login_required
def get_trend():
    start_date_str = request.args.get('start', '')
    end_date_str = request.args.get('end', '')
    
    today = datetime.now().date()
    try:
        if end_date_str:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        else:
            end_date = today
            
        if start_date_str:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        else:
            start_date = end_date - timedelta(days=6)
    except ValueError:
        end_date = today
        start_date = end_date - timedelta(days=6)
        
    if start_date > end_date:
        start_date, end_date = end_date, start_date
        
    # 为防止一次性加载过多数据卡顿，前端一般限制在90天内，这里做安全兜底
    if (end_date - start_date).days > 90:
        start_date = end_date - timedelta(days=90)
        
    delta = (end_date - start_date).days
    dates = [(start_date + timedelta(days=i)).strftime('%Y-%m-%d') for i in range(delta + 1)]
    
    query = db.session.query(
        func.substr(OpinionData.publish_time, 1, 10).label('date_str'),
        func.sum(case((OpinionData.sentiment_score >= 0.6, 1), else_=0)).label('pos'),
        func.sum(case((OpinionData.sentiment_score <= 0.4, 1), else_=0)).label('neg'),
        func.sum(case(((OpinionData.sentiment_score > 0.4) & (OpinionData.sentiment_score < 0.6), 1), else_=0)).label('neu')
    ).group_by('date_str').filter(
        OpinionData.publish_time >= start_date.strftime('%Y-%m-%d'),
        OpinionData.publish_time <= (end_date + timedelta(days=1)).strftime('%Y-%m-%d')
    ).all()
    
    data_map = {item.date_str: item for item in query}
    res_pos = [data_map[d].pos if d in data_map else 0 for d in dates]
    res_neg = [data_map[d].neg if d in data_map else 0 for d in dates]
    res_neu = [data_map[d].neu if d in data_map else 0 for d in dates]
    
    return jsonify({
        'dates': dates,
        'series': [
            {'name': '正面', 'data': res_pos},
            {'name': '中性', 'data': res_neu},
            {'name': '负面', 'data': res_neg}
        ]
    })

@app.route('/api/entities')
@login_required
def get_entities():
    all_entities = db.session.query(OpinionData.entities).filter(OpinionData.entities != None).all()
    counter = {'nr': collections.Counter(), 'ns': collections.Counter(), 'nt': collections.Counter()}
    
    # 1. 强制纠错表 (解决分类错误: 如将"华为"从[人物]挪到[公司])
    ENTITY_CORRECTION = {
        # 知名企业强制归类为 'nt' (Organization)
        "华为": "nt", "京东": "nt", "美团": "nt", "贝壳": "nt", "腾讯": "nt", "阿里": "nt",
        "百度": "nt", "字节": "nt", "小米": "nt", "滴滴": "nt", "拼多多": "nt", "比亚迪": "nt",
        "万科": "nt", "恒大": "nt", "碧桂园": "nt", "融创": "nt", "新东方": "nt", "谷歌": "nt",
        
        # 地名强制归类 (虽然通常识别较准，但防止被误标)
        "北京": "ns", "上海": "ns", "广州": "ns", "深圳": "ns", "杭州": "ns",
        
        # 人名强制归类
        "特朗普": "nr", "马斯克": "nr", "雷军": "nr"
    }

    # 2. 增强版废词库 (解决完全误识别)
    BLOCKLIST = {
        "上市", "城市", "深度", "记者", "编辑", "中国", "美国", "日本", "有限公司", "股份有限公司", "集团", "分公司",
        "网友", "警方", "官方", "专家", "人士", "负责人", "创始人", "分析师", "作者", "来源",
        "公司", "平台", "部门", "政府", "委员会", "协会", "中心", "局", "厅", "部", "网", "APP", "客户端", "品牌", "行业", "市场",
        # 用户反馈的新噪音
        "高达", "续航", "白银", "权威", "博文", "显示", "目前",
        "许可证", "荣获", "发布", "推出", "表示", "认为", "指出", "今日", "昨日", "本周",
        "智慧", "东西", "振亨", "上市公司",
        # 第三批集中过滤名单
        "元宝", "智能化", "卫星", "高峰", "官宣", "新春", "上海东方", "提升", "加快", "核心", "全国", "全省", "全市"
    }

    # 3. 实体消歧与对齐 (Entity Resolution/Alignment): 归一化聚合热度
    ENTITY_ALIGNMENT = {
        "老马": "马斯克", "埃隆·马斯克": "马斯克", "埃隆马斯克": "马斯克",
        "大强子": "刘强东", "强东": "刘强东",
        "企鹅": "腾讯", "鹅厂": "腾讯", "腾讯公司": "腾讯", "腾讯科技": "腾讯",
        "字节": "字节跳动", "头条": "字节跳动", "字节跳动科技有限公司": "字节跳动",
        "B站": "哔哩哔哩", "b站": "哔哩哔哩",
        "老美": "美国", "漂亮国": "美国",
        "苹果公司": "苹果", "Apple": "苹果",
        "Meta": "脸书", "Facebook": "脸书", "微软公司": "微软",
        "北京市": "北京", "上海市": "上海", "广州市": "广州", "深圳市": "深圳", "杭州市": "杭州", "成都市": "成都"
    }
    
    # 4. 边界清洗无效与拦截后缀特征
    BOUNDARY_PREFIXES = ("前", "某", "老", "小", "大", "约", "仅")
    BANNED_SUFFIX_PERSON = ("化", "宣", "网", "会", "春", "星", "局", "厅", "部", "科", "报")
    BANNED_SUFFIX_LOCATION = ("春", "化", "网", "会", "星", "报")

    for item in all_entities:
        try:
            if not item.entities: continue
            ent_dict = json.loads(item.entities) # 解析 JSON
            
            # 临时集合，用于处理后的去重和归类
            processed_ents = {'nr': set(), 'ns': set(), 'nt': set()}
            
            # 第一轮：遍历原始数据应用高级挖掘规则
            for tag, words in ent_dict.items():
                if tag not in ['nr', 'ns', 'nt']: continue
                for raw_word in words:
                    word = raw_word.strip() # 清理首尾空格
                    
                    # [优化项 1] 基础过滤
                    if word in BLOCKLIST or len(word) < 2: continue
                    
                    # [优化项 1.5] 启发式拦截 (Heuristic Ban) - 彻底封杀构词法错误
                    if tag == 'nr' and word.endswith(BANNED_SUFFIX_PERSON): continue
                    if tag == 'ns' and word.endswith(BANNED_SUFFIX_LOCATION): continue
                    
                    # [优化项 2] 长度异常过滤 (Length Anomaly Filtering)
                    # 人名>5字(除外籍带翻译点)、地名>7字、机构>15字大概率是模型分错了
                    if tag == 'nr' and len(word) > 5 and '·' not in word: continue
                    if tag == 'ns' and len(word) > 7: continue
                    if tag == 'nt' and len(word) > 15: continue
                    
                    # [优化项 3] 边界冗余字清洗 (Boundary Cleaning)
                    if tag in ['nr', 'ns']:  # 人名地名剥掉后缀废词最安全
                        for suffix in ["等", "指出", "表示", "说", "认为", "的", "称"]:
                            if word.endswith(suffix) and len(word) - len(suffix) >= 2:
                                word = word[:-len(suffix)]
                        for prefix in BOUNDARY_PREFIXES:
                            if word.startswith(prefix) and len(word) - len(prefix) >= 2:
                                word = word[len(prefix):]
                                
                    if tag == 'nt': # 机构剥除不必要的"公司"尾缀（便于和普通缩写聚合）
                        if word.endswith("公司") and len(word) >= 4:
                            word = word[:-2]
                            
                    # 重验清洗后的词语
                    if word in BLOCKLIST or len(word) < 2: continue
                    
                    # [优化项 4] 实体归一化消歧 (Entity Resolution)
                    if word in ENTITY_ALIGNMENT:
                        word = ENTITY_ALIGNMENT[word]
                    
                    # [优化项 5] 跨分类强制纠正
                    target_tag = tag # 默认保持原分类
                    if word in ENTITY_CORRECTION:
                        target_tag = ENTITY_CORRECTION[word]
                    
                    processed_ents[target_tag].add(word)

            # 第二轮：更新到总计数器
            for tag in ['nr', 'ns', 'nt']:
                counter[tag].update(processed_ents[tag])

        except:
            pass
    return jsonify({
        'nr': [{'name': k, 'value': v} for k, v in counter['nr'].most_common(8)],
        'ns': [{'name': k, 'value': v} for k, v in counter['ns'].most_common(8)],
        'nt': [{'name': k, 'value': v} for k, v in counter['nt'].most_common(8)],
    })

@app.route('/api/topics')
@login_required
def get_topics():
    # 增加样本量以提高聚类效果
    contents = db.session.query(OpinionData.content).order_by(desc(OpinionData.id)).limit(300).all() 
    if not contents:
        return jsonify({'topics': []})
    
    # 扩充停用词表 (去除无意义的高频词)
    STOPWORDS = {
        "什么", "一个", "我们", "你们", "这个", "那个", "因为", "所以", "如果", "虽然", 
        "但是", "可能", "觉得", "认为", "时候", "现在", "就是", "可以", "以及", "非常",
        "没有", "不是", "还是", "为了", "而且", "其中", "或者", "进行", "问题", "表示",
        "已经", "出现", "位于", "方面", "部分", "相关", "需要", "有些", "这种", "点击",
        "进入", "关注", "发布", "查看", "更多", "搜索", "推荐", "阅读", "评论", "分享",
        "图片", "视频", "显示", "来源", "作者", "编辑", "记者", "报道", "指出", "认为",
        "发现", "发展", "发生", "主要", "作为", "情况", "最新", "消息", "数据", "工作", 
        "提供", "要求", "使用", "包括", "目前", "支持", "影响", "增加", "影响", "带来",
        "具有", "达到", "成为", "这是", "的话", "之后", "之前", "一直", "开始"
    }

    # 允许的词性：名词(n, nr, ns, nt, nz, ng), 名动词(vn), 动词(v)
    ALLOWED_POS = {'n', 'nr', 'ns', 'nt', 'nz', 'ng', 'vn', 'v'}

    corpus = []
    for item in contents:
        if item.content:
            # 引入词性过滤，并过滤掉停用词
            words = []
            for word, flag in pseg.cut(item.content):
                if len(word) > 1 and word not in STOPWORDS and flag in ALLOWED_POS:
                    words.append(word)
            corpus.append(" ".join(words))
            
    if not corpus: return jsonify({'topics': []})
    
    try:
        # 升级为 TfidfVectorizer: 自动降低"烂大街"词汇的权重，提升核心业务词权重
        # max_df=0.85: 如果在超过85%的文档中出现，则认为是全局通用词忽略
        vectorizer = TfidfVectorizer(max_df=0.85, min_df=2, max_features=1000)
        tf = vectorizer.fit_transform(corpus)
        feature_names = vectorizer.get_feature_names_out()
        n_topics = 3
        lda = LatentDirichletAllocation(n_components=n_topics, max_iter=5, learning_method='online', learning_offset=50., random_state=0)
        lda.fit(tf)
        topics_result = []
        n_top_words = 8
        for topic_idx, topic in enumerate(lda.components_):
            top_words = [feature_names[i] for i in topic.argsort()[:-n_top_words - 1:-1]]
            topics_result.append({
                'id': topic_idx + 1,
                'keywords': top_words
            })
        return jsonify({'topics': topics_result})
    except Exception as e:
        print(f"LDA Error: {e}")
        return jsonify({'topics': [], 'error': str(e)})

scheduler.add_job(id='spider_job', func=scheduled_spider_task, trigger='interval', minutes=5)

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        # 初始化测试 MonitorTask (若无)
        if not MonitorTask.query.first():
            test_task = MonitorTask(keyword="小米SU7", platform="全部")
            db.session.add(test_task)
            db.session.commit()
            
    scheduler.start()
    
    # [优化] 在 Flask 启动瞬间，开启一个后台线程立刻跑一次爬虫，不用干等 5 分钟
    import threading
    threading.Thread(target=scheduled_spider_task, daemon=True).start()
    
    app.run(debug=True, port=5000, use_reloader=False)