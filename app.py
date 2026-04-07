"""
种草盒子 — AI 驱动的个人灵感收藏工具
Flask 后端，端口 8080
"""
import os
import json
import hashlib
import sqlite3
import base64
import threading
import urllib.request
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory, g

app = Flask(__name__, static_folder='static')
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), 'uploads')
app.config['DATABASE'] = os.path.join(os.path.dirname(__file__), 'zhongcao.db')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# ============ 数据库 ============

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(app.config['DATABASE'])
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_db():
    db = sqlite3.connect(app.config['DATABASE'])
    db.execute('''CREATE TABLE IF NOT EXISTS items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        image_path TEXT NOT NULL,
        image_hash TEXT,
        tags TEXT DEFAULT '',
        ai_description TEXT DEFAULT '',
        keywords TEXT DEFAULT '',
        category TEXT DEFAULT '其他',
        status TEXT DEFAULT 'want',
        ai_status TEXT DEFAULT 'pending',
        note TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS config (
        key TEXT PRIMARY KEY,
        value TEXT
    )''')
    db.commit()
    db.close()

# ============ AI 识别 ============

def get_api_config():
    """获取 API 配置（key + base_url）"""
    db = get_db()
    key_row = db.execute('SELECT value FROM config WHERE key = ?', ('openai_api_key',)).fetchone()
    url_row = db.execute('SELECT value FROM config WHERE key = ?', ('openai_base_url',)).fetchone()
    api_key = (key_row['value'] if key_row else '') or os.environ.get('OPENAI_API_KEY', '')
    base_url = (url_row['value'] if url_row else '') or os.environ.get('OPENAI_BASE_URL', '') or 'https://dashscope.aliyuncs.com/compatible-mode/v1'
    return api_key, base_url

def ai_is_zhongcao(image_path):
    """判断图片是否是种草内容（返回 True/False + 识别结果）"""
    api_key, base_url = get_api_config()
    if not api_key:
        return True, None  # 没有 API Key 时默认保留

    try:
        import openai
        client_kwargs = {'api_key': api_key}
        if base_url:
            client_kwargs['base_url'] = base_url
        client = openai.OpenAI(**client_kwargs)

        with open(image_path, 'rb') as f:
            img_data = base64.b64encode(f.read()).decode('utf-8')

        ext = os.path.splitext(image_path)[1].lower()
        mime = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
                'gif': 'image/gif', 'webp': 'image/webp'}.get(ext.lstrip('.'), 'image/jpeg')

        response = client.chat.completions.create(
            model="qwen-vl-plus-latest",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": """判断这张图片是否是"种草"内容（想买的商品、美甲参考、穿搭灵感、美妆推荐、家居好物等）。

如果不是种草（比如聊天记录、付款截图、工作截图、纯文字、二维码、通知截图等），返回：
{"is_zhongcao": false}

如果是种草，返回完整识别结果：
{"is_zhongcao": true, "category": "品类", "tags": ["标签1","标签2"], "brand": "品牌名或null", "color": "主要颜色", "style": "风格描述", "description": "一句话描述", "search_keywords": ["关键词1","关键词2"]}

返回纯JSON，不要markdown格式。"""},
                    {"type": "image_url", "image_url": {
                        "url": f"data:{mime};base64,{img_data}",
                        "detail": "low"
                    }}
                ]
            }],
            max_tokens=500
        )

        text = response.choices[0].message.content.strip()
        if text.startswith('```'):
            text = text.split('\n', 1)[1] if '\n' in text else text[3:]
            if text.endswith('```'):
                text = text[:-3]
            text = text.strip()

        result = json.loads(text)
        is_zc = result.get('is_zhongcao', True)
        return is_zc, result if is_zc else None
    except Exception as e:
        print(f"AI 筛选失败: {e}")
        return True, None  # 出错时默认保留

def ai_recognize(image_path):
    """调用 GPT-4o Vision 识别图片内容"""
    api_key, base_url = get_api_config()
    if not api_key:
        return None

    try:
        import openai
        client_kwargs = {'api_key': api_key}
        if base_url:
            client_kwargs['base_url'] = base_url
        client = openai.OpenAI(**client_kwargs)

        # 读取图片并转 base64
        with open(image_path, 'rb') as f:
            img_data = base64.b64encode(f.read()).decode('utf-8')

        # 判断图片类型
        ext = os.path.splitext(image_path)[1].lower()
        mime = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
                'gif': 'image/gif', 'webp': 'image/webp'}.get(ext.lstrip('.'), 'image/jpeg')

        response = client.chat.completions.create(
            model="qwen-vl-plus-latest",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": """你是一个种草图片识别助手。分析这张图片，返回纯JSON（中文），不要包含markdown格式：
{
  "category": "品类（美妆、穿搭、美甲、家居、美食、数码、其他）",
  "tags": ["标签1", "标签2"],
  "brand": "品牌名或null",
  "color": "主要颜色",
  "style": "风格描述",
  "description": "一句话描述种草内容",
  "search_keywords": ["关键词1", "关键词2"]
}"""},
                    {"type": "image_url", "image_url": {
                        "url": f"data:{mime};base64,{img_data}",
                        "detail": "low"
                    }}
                ]
            }],
            max_tokens=500
        )

        text = response.choices[0].message.content.strip()
        # 清理可能的 markdown 包裹
        if text.startswith('```'):
            text = text.split('\n', 1)[1] if '\n' in text else text[3:]
            if text.endswith('```'):
                text = text[:-3]
            text = text.strip()

        result = json.loads(text)
        return result
    except Exception as e:
        print(f"AI 识别失败: {e}")
        return None

# ============ 路由 ============

@app.route('/api/quick-upload', methods=['POST'])
def quick_upload():
    """快捷指令专用接口：接收图片，返回简单结果"""
    # 支持两种方式：multipart file 或 raw body
    if request.files.get('image'):
        f = request.files['image']
        content = f.read()
        ext = os.path.splitext(f.filename)[1].lower() or '.jpg'
    elif request.data:
        content = request.data
        ext = '.jpg'
    else:
        return '没有图片', 400

    file_hash = hashlib.md5(content).hexdigest()

    # 去重
    with app.app_context():
        db = get_db()
        existing = db.execute('SELECT id FROM items WHERE image_hash = ?', (file_hash,)).fetchone()
        if existing:
            return '已存过啦~'

        filename = f"{file_hash}{ext}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        with open(filepath, 'wb') as out:
            out.write(content)

        db.execute(
            'INSERT INTO items (image_path, image_hash, ai_status) VALUES (?, ?, ?)',
            (filename, file_hash, 'pending')
        )
        db.commit()
        item_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]

        # AI 识别
        ai_result = ai_recognize(filepath)
        if ai_result:
            tags = ','.join(ai_result.get('tags', []))
            keywords = ','.join(ai_result.get('search_keywords', []))
            brand = ai_result.get('brand') or ''
            if brand and brand != 'null':
                keywords = f"{brand},{keywords}" if keywords else brand
            desc = ai_result.get('description', '')
            category = ai_result.get('category', '其他')
            db.execute('''UPDATE items SET tags=?, ai_description=?, keywords=?,
                         category=?, ai_status='done' WHERE id=?''',
                       (tags, desc, keywords, category, item_id))
            db.commit()
            return f'已种草！{category} - {desc}'
        else:
            return '已保存（AI 识别稍后补充）'

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/api/upload', methods=['POST'])
def upload():
    """上传图片，支持多张"""
    files = request.files.getlist('images')
    if not files:
        return jsonify({'error': '没有选择图片'}), 400

    results = []
    db = get_db()

    for f in files:
        if not f.filename:
            continue

        # 计算哈希去重
        content = f.read()
        file_hash = hashlib.md5(content).hexdigest()
        f.seek(0)

        existing = db.execute('SELECT id FROM items WHERE image_hash = ?', (file_hash,)).fetchone()
        if existing:
            results.append({'id': existing['id'], 'status': 'duplicate'})
            continue

        # 保存文件
        ext = os.path.splitext(f.filename)[1].lower() or '.jpg'
        filename = f"{file_hash}{ext}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)

        with open(filepath, 'wb') as out:
            out.write(content)

        # 插入数据库
        db.execute(
            'INSERT INTO items (image_path, image_hash, ai_status) VALUES (?, ?, ?)',
            (filename, file_hash, 'pending')
        )
        db.commit()
        item_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]

        # 尝试 AI 识别
        ai_result = ai_recognize(filepath)
        if ai_result:
            tags = ','.join(ai_result.get('tags', []))
            keywords = ','.join(ai_result.get('search_keywords', []))
            brand = ai_result.get('brand') or ''
            if brand and brand != 'null':
                keywords = f"{brand},{keywords}" if keywords else brand
            db.execute('''UPDATE items SET tags=?, ai_description=?, keywords=?,
                         category=?, ai_status='done' WHERE id=?''',
                       (tags, ai_result.get('description', ''), keywords,
                        ai_result.get('category', '其他'), item_id))
            db.commit()
            results.append({'id': item_id, 'status': 'ok', 'ai': True})
        else:
            results.append({'id': item_id, 'status': 'ok', 'ai': False})

    return jsonify({'results': results})

@app.route('/api/items')
def list_items():
    """获取所有种草，支持筛选和搜索"""
    db = get_db()
    category = request.args.get('category', '')
    status = request.args.get('status', '')
    query = request.args.get('q', '')

    sql = 'SELECT * FROM items WHERE 1=1'
    params = []

    if category and category != '全部':
        sql += ' AND category = ?'
        params.append(category)

    if status and status != '全部':
        sql += ' AND status = ?'
        params.append(status)

    if query:
        sql += ''' AND (tags LIKE ? OR ai_description LIKE ? OR keywords LIKE ?
                   OR category LIKE ? OR note LIKE ?)'''
        q = f'%{query}%'
        params.extend([q, q, q, q, q])

    sql += ' ORDER BY created_at DESC'
    rows = db.execute(sql, params).fetchall()

    items = []
    for row in rows:
        items.append({
            'id': row['id'],
            'image_path': row['image_path'],
            'tags': row['tags'],
            'ai_description': row['ai_description'],
            'keywords': row['keywords'],
            'category': row['category'],
            'status': row['status'],
            'ai_status': row['ai_status'],
            'note': row['note'],
            'created_at': row['created_at']
        })

    return jsonify({'items': items})

@app.route('/api/items/<int:item_id>', methods=['PATCH'])
def update_item(item_id):
    """更新种草状态、标签、备注"""
    db = get_db()
    data = request.json

    updates = []
    params = []
    for field in ['status', 'tags', 'category', 'note']:
        if field in data:
            updates.append(f'{field} = ?')
            params.append(data[field])

    if not updates:
        return jsonify({'error': '没有要更新的字段'}), 400

    params.append(item_id)
    db.execute(f'UPDATE items SET {", ".join(updates)} WHERE id = ?', params)
    db.commit()
    return jsonify({'ok': True})

@app.route('/api/items/<int:item_id>', methods=['DELETE'])
def delete_item(item_id):
    """删除种草"""
    db = get_db()
    row = db.execute('SELECT image_path FROM items WHERE id = ?', (item_id,)).fetchone()
    if row:
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], row['image_path'])
        if os.path.exists(filepath):
            os.remove(filepath)
        db.execute('DELETE FROM items WHERE id = ?', (item_id,))
        db.commit()
    return jsonify({'ok': True})

@app.route('/api/categories')
def categories():
    """获取所有品类及数量"""
    db = get_db()
    rows = db.execute('SELECT category, COUNT(*) as count FROM items GROUP BY category ORDER BY count DESC').fetchall()
    cats = [{'name': row['category'], 'count': row['count']} for row in rows]
    total = sum(c['count'] for c in cats)
    cats.insert(0, {'name': '全部', 'count': total})
    return jsonify({'categories': cats})

@app.route('/api/stats')
def stats():
    """统计数据"""
    db = get_db()
    total = db.execute('SELECT COUNT(*) FROM items').fetchone()[0]
    want = db.execute("SELECT COUNT(*) FROM items WHERE status='want'").fetchone()[0]
    bought = db.execute("SELECT COUNT(*) FROM items WHERE status='bought'").fetchone()[0]
    done = db.execute("SELECT COUNT(*) FROM items WHERE status='done'").fetchone()[0]
    ai_done = db.execute("SELECT COUNT(*) FROM items WHERE ai_status='done'").fetchone()[0]
    return jsonify({'total': total, 'want': want, 'bought': bought, 'done': done, 'ai_done': ai_done})

@app.route('/api/config', methods=['GET', 'POST'])
def config():
    """管理配置（API Key 等）"""
    db = get_db()
    if request.method == 'POST':
        data = request.json
        key = data.get('key')
        value = data.get('value', '')
        db.execute('INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)', (key, value))
        db.commit()
        return jsonify({'ok': True})
    else:
        key_row = db.execute('SELECT value FROM config WHERE key = ?', ('openai_api_key',)).fetchone()
        url_row = db.execute('SELECT value FROM config WHERE key = ?', ('openai_base_url',)).fetchone()
        has_key = bool(key_row and key_row['value'])
        base_url = (url_row['value'] if url_row else '') or ''
        return jsonify({'has_api_key': has_key, 'base_url': base_url})

@app.route('/api/retry-ai/<int:item_id>', methods=['POST'])
def retry_ai(item_id):
    """重新 AI 识别"""
    db = get_db()
    row = db.execute('SELECT image_path FROM items WHERE id = ?', (item_id,)).fetchone()
    if not row:
        return jsonify({'error': '图片不存在'}), 404

    filepath = os.path.join(app.config['UPLOAD_FOLDER'], row['image_path'])
    ai_result = ai_recognize(filepath)
    if ai_result:
        tags = ','.join(ai_result.get('tags', []))
        keywords = ','.join(ai_result.get('search_keywords', []))
        brand = ai_result.get('brand') or ''
        if brand and brand != 'null':
            keywords = f"{brand},{keywords}" if keywords else brand
        db.execute('''UPDATE items SET tags=?, ai_description=?, keywords=?,
                     category=?, ai_status='done' WHERE id=?''',
                   (tags, ai_result.get('description', ''), keywords,
                    ai_result.get('category', '其他'), item_id))
        db.commit()
        return jsonify({'ok': True, 'ai': True})
    else:
        return jsonify({'ok': False, 'ai': False, 'error': '识别失败，请检查 API Key'})

@app.route('/api/scan-folder', methods=['POST'])
def scan_folder():
    """扫描指定文件夹，自动导入新图片"""
    data = request.json or {}
    folder = data.get('folder', '')
    if not folder or not os.path.isdir(folder):
        return jsonify({'error': '文件夹不存在'}), 400

    db = get_db()
    imported = 0
    skipped = 0

    for fname in os.listdir(folder):
        ext = os.path.splitext(fname)[1].lower()
        if ext not in ('.jpg', '.jpeg', '.png', '.heic', '.webp'):
            continue

        src = os.path.join(folder, fname)
        with open(src, 'rb') as f:
            content = f.read()

        file_hash = hashlib.md5(content).hexdigest()
        existing = db.execute('SELECT id FROM items WHERE image_hash = ?', (file_hash,)).fetchone()
        if existing:
            skipped += 1
            continue

        dest_name = f"{file_hash}{ext}"
        dest_path = os.path.join(app.config['UPLOAD_FOLDER'], dest_name)
        with open(dest_path, 'wb') as out:
            out.write(content)

        db.execute(
            'INSERT INTO items (image_path, image_hash, ai_status) VALUES (?, ?, ?)',
            (dest_name, file_hash, 'pending')
        )
        db.commit()
        item_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]

        ai_result = ai_recognize(dest_path)
        if ai_result:
            tags = ','.join(ai_result.get('tags', []))
            keywords = ','.join(ai_result.get('search_keywords', []))
            brand = ai_result.get('brand') or ''
            if brand and brand != 'null':
                keywords = f"{brand},{keywords}" if keywords else brand
            db.execute('''UPDATE items SET tags=?, ai_description=?, keywords=?,
                         category=?, ai_status='done' WHERE id=?''',
                       (tags, ai_result.get('description', ''), keywords,
                        ai_result.get('category', '其他'), item_id))
            db.commit()

        imported += 1

    return jsonify({'imported': imported, 'skipped': skipped})

init_db()

# 防止 Render 免费套餐休眠：每 10 分钟 ping 自己
def keep_alive():
    import time
    url = os.environ.get('RENDER_EXTERNAL_URL', '')
    while url:
        try:
            urllib.request.urlopen(url + '/api/stats', timeout=10)
        except Exception:
            pass
        time.sleep(600)

if os.environ.get('RENDER'):
    threading.Thread(target=keep_alive, daemon=True).start()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8081))
    print(f"🌱 种草盒子启动！访问 http://localhost:{port}")
    app.run(host='0.0.0.0', port=port, debug=True)
