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
    api_key = (key_row['value'] if key_row else '') or os.environ.get('OPENAI_API_KEY', '') or 'sk-1a64dd3bb4d442d4a57927a1c296c2a8'
    base_url = (url_row['value'] if url_row else '') or os.environ.get('OPENAI_BASE_URL', '') or 'https://dashscope.aliyuncs.com/compatible-mode/v1'
    return api_key, base_url

CATEGORIES = ['穿搭', '美甲', '美妆', '食品', '家居', '数码', '其他']

def ai_classify(image_path):
    """轻量 AI 分类：只返回一个分类词"""
    api_key, base_url = get_api_config()
    if not api_key:
        return '其他'

    try:
        import openai
        client = openai.OpenAI(api_key=api_key, base_url=base_url)

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
                    {"type": "text", "text": """请判断图片分类，只回复一个分类词。

分类表：
穿搭：衣服、裤子、裙子、鞋子、包包、帽子、围巾、配饰、首饰、手表、穿搭搭配图
美甲：指甲、美甲款式、美甲图片
美妆：口红、眼影、粉底、护肤品、香水、化妆品、妆容图
食品：零食、饮料、蛋糕、餐厅、美食图片
家居：家具、装修、收纳、灯具、床品、家居用品
数码：手机、电脑、耳机、相机、电子产品

只回复分类词，不要解释。"""},
                    {"type": "image_url", "image_url": {
                        "url": f"data:{mime};base64,{img_data}",
                        "detail": "low"
                    }}
                ]
            }],
            max_tokens=10
        )

        text = response.choices[0].message.content.strip()
        # 匹配到已知分类
        for cat in CATEGORIES:
            if cat in text:
                return cat
        return '其他'
    except Exception as e:
        print(f"AI 分类失败: {e}")
        return '其他'

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

        return '已保存！'

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/thumb/<path:filename>')
def thumbnail(filename):
    """返回缩略图，列表页加载更快"""
    thumb_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'thumbs')
    thumb_path = os.path.join(thumb_dir, filename)
    if not os.path.exists(thumb_path):
        os.makedirs(thumb_dir, exist_ok=True)
        src = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        if not os.path.exists(src):
            return '', 404
        try:
            from PIL import Image
            img = Image.open(src)
            img.thumbnail((800, 800))
            img.save(thumb_path, 'JPEG', quality=85)
        except Exception:
            return send_from_directory(app.config['UPLOAD_FOLDER'], filename)
    return send_from_directory(thumb_dir, filename)

def bg_ai_classify(item_id, filepath):
    """后台线程执行 AI 分类"""
    try:
        category = ai_classify(filepath)
        db = sqlite3.connect(app.config['DATABASE'])
        db.execute('UPDATE items SET category=?, ai_status=? WHERE id=?',
                   (category, 'done', item_id))
        db.commit()
        db.close()
    except Exception as e:
        print(f"后台 AI 分类失败: {e}")

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
    for field in ['status', 'tags', 'category', 'note', 'ai_status']:
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
        api_key, base_url = get_api_config()
        return jsonify({'has_api_key': bool(api_key), 'base_url': base_url})

@app.route('/api/retry-ai/<int:item_id>', methods=['POST'])
def retry_ai(item_id):
    """重新 AI 识别"""
    db = get_db()
    row = db.execute('SELECT image_path FROM items WHERE id = ?', (item_id,)).fetchone()
    if not row:
        return jsonify({'error': '图片不存在'}), 404

    filepath = os.path.join(app.config['UPLOAD_FOLDER'], row['image_path'])
    category = ai_classify(filepath)
    db.execute('UPDATE items SET category=?, ai_status=? WHERE id=?',
               (category, 'done', item_id))
    db.commit()
    return jsonify({'ok': True, 'category': category})

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

        category = ai_classify(dest_path)
        db.execute('UPDATE items SET category=?, ai_status=? WHERE id=?',
                   (category, 'done', item_id))
        db.commit()

        imported += 1

    return jsonify({'imported': imported, 'skipped': skipped})

init_db()

# 启动时清理旧缩略图缓存（参数调整后需要重新生成）
import shutil
thumb_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'thumbs')
if os.path.exists(thumb_dir):
    shutil.rmtree(thumb_dir)

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
