# sentiment_analysis.py
import jieba

class SentimentAnalyzer:
    def __init__(self):
        # 1. 初始化词典 (这里内置一个精简版的基础词典，实际生产中应加载外部大词典文件)
        self.pos_words = {
            "上涨", "增长", "突破", "创新", "优秀", "成功", "利好", "支持", "赞赏", "不仅",
            "而且", "非常", "重要", "显著", "提升", "优化", "改善", "加强", "推动", "促进",
            "领先", "核心", "关键", "新高", "盈利", "获利", "大涨", "看好", "推荐", "买入",
            "只有", "才", "能", "好", "强", "棒", "牛", "厉害", "先进", "稳定", "复苏",
            "回暖", "积极", "贡献", "成就", "辉煌", "榜首", "冠军", "首选", "满意"
        }
        
        self.neg_words = {
            "下跌", "亏损", "暴跌", "失望", "垃圾", "谴责", "担忧", "风险", "警告", "危机",
            "下滑", "减少", "衰退", "低迷", "疲软", "冲击", "损失", "损害", "不仅", "而且",
            "非常", "严重", "恶化", "不足", "缺陷", "漏洞", "失败", "崩溃", "腰斩", "看空",
            "卖出", "差", "弱", "烂", "糟糕", "落后", "动荡", "萧条", "消极", "破坏",
            "灾难", "惨淡", "垫底", "投诉", "不满", "抗议", "制裁", "调查", "违规"
        }
        
        # 否定词
        self.not_words = {
            "不", "没", "无", "非", "莫", "弗", "勿", "毋", "未", "否", "别", "无一", "不再"
        }
        
        # 程度副词及其权重
        self.degree_words = {
            "极其": 2.0, "九分": 1.8, "十分": 1.8, "非常": 1.5, "很": 1.5, "特别": 1.5,
            "相当": 1.4, "过于": 1.4, "太": 1.4, "更加": 1.3, "更": 1.3, "比较": 1.2,
            "稍微": 1.1, "有点": 1.1, "一点": 1.1
        }

    def analyze(self, text):
        """
        计算文本情感分数
        返回: 0.0 (极负) ~ 1.0 (极正)
        """
        if not text: return 0.5
        
        words = list(jieba.cut(text))
        score = 0
        weight = 1       # 词权重，受程度副词影响
        negation = 1     # 否定状态，1为肯定，-1为否定
        
        # 简单的滑动窗口或逐词扫描
        # 这里采用简化的逐词扫描 + 向前看逻辑
        
        i = 0
        while i < len(words):
            word = words[i]
            
            # 1. 检查是否是程度副词 (更新权重)
            if word in self.degree_words:
                weight = self.degree_words[word]
                # 继续看下一个词
                i += 1
                continue
                
            # 2. 检查是否是否定词 (翻转极性)
            if word in self.not_words:
                negation *= -1
                # 否定词通常不重置权重，或者轻微衰减权重？简单模型保持权重
                i += 1
                continue
            
            # 3. 计算情感分
            word_score = 0
            if word in self.pos_words:
                word_score = 1
            elif word in self.neg_words:
                word_score = -1
            
            if word_score != 0:
                # 累加分数: 词分 * 否定 * 权重
                score += word_score * negation * weight
                
                # 结算完一个情感词后，重置状态
                weight = 1
                negation = 1
            
            # 如果碰到了标点符号，也可以重置状态
            if word in ["，", "。", "！", "？", " ", "\n"]:
                weight = 1
                negation = 1
                
            i += 1

        # 将并归一化到 0~1 之间
        # 假设一篇普通文章的分数通常在 -10 到 10 之间，我们用 sigmoid 或简单的线性映射
        # 这里使用简单的线性收缩: result = 0.5 + (score / 20)
        # 限制在 0.1 ~ 0.9 之间，避免太极端
        
        norm_score = 0.5 + (score / 40.0) # 分母越大，分数越集中在0.5附近
        if norm_score > 0.99: norm_score = 0.99
        if norm_score < 0.01: norm_score = 0.01
        
        return norm_score

# 单例实例
analyzer = SentimentAnalyzer()
