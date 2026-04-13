# spider.py
import requests
from bs4 import BeautifulSoup
import random
import time
from datetime import datetime
from app import app
from models import db, OpinionData, AlertConfig
import traceback
import email_service
import re
import jieba.analyse
import jieba.posseg as pseg
from snownlp import SnowNLP
import json
import concurrent.futures

# 1. 种子 URL 列表
START_URLS = [
    "https://tech.sina.com.cn/",          # 新浪科技
    "https://auto.sina.com.cn/",          # 新浪汽车
    "https://www.sohu.com/c/8",            # 搜狐科技
    "https://tech.163.com/",               # 网易科技
    "https://new.qq.com/ch/tech/",         # 腾讯科技
    "https://tech.ifeng.com/",             # 凤凰科技
    "https://www.thepaper.cn/",            # 澎湃新闻
    "https://www.36kr.com/",               # 36氪科技
]

# 4. 伪装：定义 User-Agent 列表
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0"
]

def get_random_header(referer="https://www.baidu.com/"):
    """生成随机 Header 以伪装爬虫"""
    if "k.sina.com.cn" in referer or "sina.cn" in referer:
         pass

    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Referer": referer
    }

def fetch_url(url, referer="https://www.baidu.com/"):
    """封装请求函数"""
    try:
        sleep_time = random.uniform(1.5, 3.5)
        print(f"等待 {sleep_time:.2f} 秒...")
        time.sleep(sleep_time)

        print(f"正在请求: {url}")
        # 保留 verify=False 避免代理软件导致的 SSL 证书未绑定报错
        try:
            # 开启 VPN/代理 的情况: 试着让它正常走底层系统代理
            response = requests.get(url, headers=get_random_header(referer), timeout=15, verify=False)
        except requests.exceptions.ProxyError:
            print("  > 监测到本地代理网络配置冲突，启动直连/TUN接管降级策略...")
            # 当出现 FileNotFoundError 那种怪异的 VPN/代理报错时，直接绕过 HTTP Proxies，
            # 此时如果开启了 TUN 虚拟网卡模式，TUN 底层依旧可以完美接管拦截。
            response = requests.get(url, headers=get_random_header(referer), timeout=15, verify=False, proxies={"http": None, "https": None})
        
        if response.status_code == 200:
            # 自动检测编码，解决乱码问题
            response.encoding = response.apparent_encoding
            return response.text
        elif response.status_code == 404:
            print(f"警告: 页面未找到 (404): {url}")
        elif response.status_code == 403:
            print(f"警告: 访问被拒绝 (403): {url}")
        else:
            print(f"警告: 请求失败 {response.status_code}: {url}")
        return None

    except Exception as e:
        print(f"错误: 请求异常: {e}")
        return None

from sentiment_analysis import analyzer

def analyze_sentiment(content):
    """
    使用自定义词典进行情感分析 (优于 SnowNLP)
    返回: 0-1 之间的分数
    """
    try:
        return analyzer.analyze(content)
    except Exception as e:
        print(f"情感分析失败: {e}")
        return 0.5

def extract_keywords(content, top_k=5):
    """使用 Jieba 提取关键词"""
    try:
        if not content: return []
        keywords = jieba.analyse.extract_tags(content, topK=top_k)
        return keywords
    except Exception as e:
        print(f"关键词提取失败: {e}")
        return []

# 全局变量缓存 LTP 模型，避免重复加载
ltp_model = None

def extract_entities(content):
    """
    使用 LTP (哈工大) 进行深度学习 NER 提取 (如有)，否则降级使用 Jieba
    LTP 实体标签: nh(人名), ns(地名), ni(机构名)
    """
    global ltp_model
    entities = {'nr': set(), 'ns': set(), 'nt': set()}
    
    # --- 尝试使用 LTP (方案二: 高精度) ---
    try:
        from ltp import LTP
        if ltp_model is None:
            print(">>> 正在加载 LTP 模型 (首次运行需下载模型, 请耐心等待)...")
            ltp_model = LTP() # 默认加载 Small 模型
            
            # 加载自定义词典 (如果有)
            try:
                ltp_model.add_words(words=["OpenAI", "ChatGPT", "生成式AI", "大语言模型", "元宇宙", "英伟达", "马斯克", "雷军", "比亚迪", "宁德时代", "拼多多", "字节跳动"], max_window=4)
                print("  [LTP] 已加载自定义词典 (优化分词)")
            except Exception as e:
                print(f"  [LTP] 自定义词典加载警告: {e}")
            
        # 限制长度防止显存溢出/超时
        short_content = content[:500]
        output = ltp_model.pipeline([short_content], tasks=["cws", "ner"])
        # output.ner 格式: [[('Nh', '人名', start, end), ...]]
        
        ner_results = output.ner[0] # 取第一句(其实是整段)的结果
        
        for role, text, start, end in ner_results:
            if role == 'Nh': # Person
                entities['nr'].add(text)
            elif role == 'Ns': # Location
                entities['ns'].add(text)
            elif role == 'Ni': # Organization
                entities['nt'].add(text)
                
        print(f"  [LTP] 识别实体: {len(entities['nr'])}人 {len(entities['ns'])}地 {len(entities['nt'])}司")
        
        # 仍然使用排除词库过滤一遍 LTP 的结果 (双重保险)
        BLOCKLIST = {
             "上市", "城市", "深度", "记者", "编辑", "中国", "美国", "日本", "有限公司", "股份有限公司", "集团", "分公司",
            "网友", "警方", "官方", "专家", "人士", "负责人", "创始人", "分析师", "作者", "来源",
            "公司", "平台", "部门", "政府", "委员会", "协会", "中心", "局", "厅", "部", "网", "APP", "客户端", "品牌", "行业", "市场",
            "高达", "续航", "白银", "权威", "博文", "显示", "目前" # 针对用户反馈的特定误识别词
        }
        
        final_entities = {'nr': [], 'ns': [], 'nt': []}
        for tag in ['nr', 'ns', 'nt']:
            for word in entities[tag]:
                if word not in BLOCKLIST and len(word) > 1:
                    final_entities[tag].append(word)
        
        return final_entities

    except ImportError:
        # --- 降级方案: Jieba (方案一: 兼容性) ---
        if "Jieba" not in str(ltp_model): # 避免重复打印
            print("提示: 未检测到 ltp 库，将使用 Jieba 进行实体识别 (运行 `pip install ltp` 可开启高精度模式)")
            ltp_model = "Jieba Placeholder" # 标记已尝试过
            
    except Exception as e:
        print(f"LTP 运行出错 ({e})，降级使用 Jieba")

    # --- 原有的 Jieba 逻辑 ---
    try:
        BLOCKLIST = {
            "上市", "城市", "深度", "记者", "编辑", "中国", "美国", "日本", "有限公司", "股份有限公司", "集团", "分公司",
            "网友", "警方", "官方", "专家", "人士", "负责人", "创始人", "分析师", "作者", "来源", # 人物排除
            "公司", "平台", "部门", "政府", "委员会", "协会", "中心", "局", "厅", "部", "网", "APP", "客户端", "品牌", "行业", "市场" # 机构/通用排除
        }
        
        words = pseg.cut(content[:500]) 
        for word, flag in words:
            if flag in ['nr', 'ns', 'nt'] and len(word) > 1 and word not in BLOCKLIST:
                 entities[flag].add(word)
        
        return {
            'nr': list(entities['nr']),
            'ns': list(entities['ns']),
            'nt': list(entities['nt'])
        }
    except Exception as e:
        print(f"NER 提取失败: {e}")
        return {'nr': [], 'ns': [], 'nt': []}

def parse_and_save(url, html):
    """解析 HTML 并保存数据"""
    if not html: return

    soup = BeautifulSoup(html, 'html.parser')
    
    try:
        # --- 标题提取 ---
        title = "未知标题"
        h1 = soup.find('h1', class_='main-title') or \
             soup.find('h1', class_='art_tit_h1') or \
             soup.find('h1')
        if h1: title = h1.get_text(strip=True)
        
        if title in ["新浪汽车", "新浪科技", "搜狐"] or len(title) < 4:
             title_tag = soup.find('title')
             if title_tag:
                raw_title = title_tag.get_text(strip=True)
                title = raw_title.split('_')[0].split('-')[0]

        if title in ["新浪汽车", "新浪科技", "手机新浪网", "未知标题"] or len(title) < 5:
            print(f"跳过: 无法有效提取标题 ({title}) - {url}")
            return

        # --- [优化] 正文提取前进行“网页干洗” (Denoising) ---
        for element in soup(['script', 'style', 'header', 'footer', 'nav', 'aside', 'iframe']):
            element.decompose() # 彻底粉碎无用标签，防止混入脏词

        content = ""
        content_selectors = [
            {'id': 'artibody'}, {'class_': 'article-body'}, {'class_': 'article'}, 
            {'class_': 'main-text'}, {'class_': 'text'}, {'itemprop': 'articleBody'}
        ]
        
        content_div = None
        for selector in content_selectors:
            content_div = soup.find('div', **selector) or soup.find('article', **selector)
            if content_div: break
        
        if content_div:
            paragraphs = content_div.find_all('p')
            content = "\n".join([p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 5])
        else:
            all_p = soup.find_all('p')
            valid_p = [p.get_text(strip=True) for p in all_p if len(p.get_text(strip=True)) > 20]
            if len(valid_p) > 2: content = "\n".join(valid_p)

        if not content or len(content) < 50:
            print(f"跳过: 正文内容过少 ({len(content)}字) - {url}")
            return

        # --- NLP 分析 ---
        sentiment_score = analyze_sentiment(content)
        keywords_str = ",".join(extract_keywords(content))
        entities = extract_entities(content)
        entities_json = json.dumps(entities, ensure_ascii=False)
        
        # 自动摘要
        summary = ""
        try:
            s = SnowNLP(content)
            summary = "。".join(s.summary(3)) + "。"
        except:
            summary = content[:100] + "..."

        print(f"  > 情感: {sentiment_score:.2f}, 实体: {len(entities['nr'])}名 {len(entities['ns'])}地 {len(entities['nt'])}司")

        # --- [优化] 真实发布时间提取 (True Publish Time) ---
        publish_time = ""
        # 1. 尝试寻找 meta 发布时间
        meta_time = soup.find('meta', {'property': 'article:published_time'}) or soup.find('meta', {'name': 'publishdate'})
        if meta_time and meta_time.get('content'):
            raw_time_str = meta_time.get('content')[:19].replace('T', ' ')
            # 过滤非结构化字符串
            if re.match(r'202\d-\d{2}-\d{2}', raw_time_str):
                publish_time = raw_time_str
                
        # 2. 正则兜底截获文内时间
        if not publish_time:
            time_match = re.search(r'202[0-9]-[01][0-9]-[0-3][0-9]\s+[0-2][0-9]:[0-5][0-9]:[0-5][0-9]', html)
            if time_match:
                publish_time = time_match.group(0)
            else:
                publish_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # --- 入库 ---
        with app.app_context():
            if OpinionData.query.filter_by(title=title).first():
                print(f"跳过: 数据已存在 - {title[:15]}...")
                return

            source = "其他"
            if "sina" in url: source = "新浪新闻"
            elif "sohu" in url: source = "搜狐新闻"
            elif "163.com" in url: source = "网易新闻"
            elif "qq.com" in url: source = "腾讯新闻"
            elif "ifeng.com" in url: source = "凤凰新闻"
            elif "thepaper.cn" in url: source = "澎湃新闻"
            elif "36kr.com" in url: source = "36氪"

            new_data = OpinionData(
                source_platform=source,
                keyword="自动采集",
                title=title,
                content=content,
                publish_time=publish_time,
                sentiment_score=sentiment_score,
                keywords=keywords_str,
                summary=summary,
                entities=entities_json # 保存 NER 结果
            )
            
            db.session.add(new_data)
            db.session.commit()
            print(f"★ 成功入库: {title[:20]}... (情感: {sentiment_score:.2f})")
            
            # --- [优化] 自动化告警守护进程介入 (满足论文预警逻辑) ---
            alert_conf = AlertConfig.query.first()
            if alert_conf and alert_conf.is_enabled and alert_conf.recipient_email:
                if sentiment_score <= alert_conf.threshold:
                    import threading
                    # 采用异步子线程发射邮件，不阻塞爬虫主 IO 性能
                    threading.Thread(
                        target=email_service.send_alert_email, 
                        args=(alert_conf.recipient_email, alert_conf.threshold, title, round(sentiment_score, 2), summary), 
                        daemon=True
                    ).start()
                    

    except Exception as e:
        print(f"解析入库错误: {e}")
        traceback.print_exc()

def extract_article_links(base_url, html):
    """提取文章链接"""
    if not html: return []
    soup = BeautifulSoup(html, 'html.parser')
    links = set()
    for a in soup.find_all('a', href=True):
        href = a['href']
        if href.startswith('//'): href = 'https:' + href
        elif href.startswith('/'): href = base_url.rstrip('/') + href
        if not href.startswith('http'): continue
        
        # 针对不同网站的链接特征进行匹配
        if "sina.com.cn" in base_url or "sina.cn" in base_url:
            if re.search(r'doc-|article_|/202\d/', href): links.add(href)
        elif "sohu.com" in base_url:
            if "/a/" in href: links.add(href)
        elif "163.com" in base_url:
            if re.search(r'/\d{2}/\d{4}/\d{2}/', href): links.add(href)
        elif "qq.com" in base_url:
            if re.search(r'/omn/\d{8}/', href): links.add(href)
        elif "ifeng.com" in base_url:
            if re.search(r'/c/\w+', href): links.add(href)
        elif "thepaper.cn" in base_url:
            if re.search(r'/newsDetail_forward_\d+', href): links.add(href)
        elif "36kr.com" in base_url:
            if "/p/" in href: links.add(href)
    
    valid_links = list(links)
    random.shuffle(valid_links)
    return valid_links[:10]  # 每个频道首页抓取10篇文章

def process_article(url, referer):
    """独立线程抓包与解析逻辑"""
    html = fetch_url(url, referer=referer)
    parse_and_save(url, html)

def run_spider():
    print(">>> 爬虫 v4.0 启动 (异步多线程/网页干洗版)...")
    requests.packages.urllib3.disable_warnings()
    print("提示: 当前已实装多线程提速架构与真实时间溯源机制。")
    
    all_target_urls = []
    
    # 步骤1：单线程扫描各个首地面（防止被主站快速拉黑）
    for seed_url in START_URLS:
        print(f"\n>>> 正在雷达扫描频道: {seed_url}")
        index_html = fetch_url(seed_url)
        if not index_html: continue
        article_urls = extract_article_links(seed_url, index_html)
        print(f"拦截到 {len(article_urls)} 个文章载荷")
        for url in article_urls:
            all_target_urls.append((url, seed_url))
            
    # 步骤2：多线程并发解析正文入库（极限压榨 IO 去下载和分析）
    print(f"\n>>> 扫描统合完毕，共计锁定 {len(all_target_urls)} 篇文章，开启 5 线程池进行深度下钻采集...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(process_article, url, referer) for url, referer in all_target_urls]
        concurrent.futures.wait(futures)
        
    print("\n>>> 所有采集通道均已关闭，任务结束。")

if __name__ == '__main__':
    run_spider()