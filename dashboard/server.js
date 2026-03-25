const express = require('express');
const Database = require('better-sqlite3');
const path = require('path');
const fs = require('fs');
const { execSync } = require('child_process');
const https = require('https');

const app = express();
const PORT = 3001;
const DB_PATH = path.join(__dirname, '..', 'data', 'wanglogistic.db');
const CONFIG_PATH = path.join(__dirname, '..', 'bot', 'config.py');

const DASH_USER = 'root';
const DASH_PASS = process.env.DASH_PASS || 'QGl5h15At08H';
const ALLOWED_ORIGIN = 'https://185-125-103-221.sslip.io';
const AUDIT_LOG_PATH = path.join(__dirname, '..', 'data', 'audit.log');

// ---- Audit log ----
function auditLog(ip, action, details = '') {
  const line = `[${new Date().toISOString()}] ${ip} ${action}${details ? ' | ' + details : ''}\n`;
  try { fs.appendFileSync(AUDIT_LOG_PATH, line); } catch (e) {}
}

// ---- Rate limiters (in-memory) ----
const authFailMap = new Map();   // ip → { count, blockedUntil }
const reqRateMap  = new Map();   // ip → { count, resetAt }

function getIp(req) {
  return (req.headers['x-real-ip'] || req.socket.remoteAddress || '').toString();
}

function isAuthBlocked(ip) {
  const entry = authFailMap.get(ip);
  if (!entry) return false;
  if (entry.blockedUntil && Date.now() < entry.blockedUntil) return true;
  if (entry.blockedUntil && Date.now() >= entry.blockedUntil) { authFailMap.delete(ip); return false; }
  return false;
}

function recordAuthFail(ip) {
  const now = Date.now();
  const entry = authFailMap.get(ip) || { count: 0, windowStart: now, blockedUntil: null };
  if (now - entry.windowStart > 15 * 60 * 1000) { entry.count = 0; entry.windowStart = now; }
  entry.count++;
  if (entry.count >= 10) { entry.blockedUntil = now + 30 * 60 * 1000; auditLog(ip, 'AUTH_BLOCKED', `${entry.count} failed attempts`); }
  authFailMap.set(ip, entry);
}

function checkReqRate(ip) {
  const now = Date.now();
  const entry = reqRateMap.get(ip) || { count: 0, resetAt: now + 60 * 1000 };
  if (now > entry.resetAt) { entry.count = 0; entry.resetAt = now + 60 * 1000; }
  entry.count++;
  reqRateMap.set(ip, entry);
  return entry.count <= 200;
}

app.use(express.json());

// Security headers
app.use((req, res, next) => {
  res.setHeader('X-Content-Type-Options', 'nosniff');
  res.setHeader('X-Frame-Options', 'DENY');
  res.setHeader('Content-Security-Policy', "default-src 'self' 'unsafe-inline' 'unsafe-eval' https:; img-src 'self' data: https:;");
  if (req.path.startsWith('/api/')) res.setHeader('Cache-Control', 'no-store');
  next();
});

// CORS
app.use((req, res, next) => {
  res.header('Access-Control-Allow-Origin', ALLOWED_ORIGIN);
  res.header('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE');
  res.header('Access-Control-Allow-Headers', 'Content-Type,Authorization');
  if (req.method === 'OPTIONS') return res.sendStatus(200);
  next();
});

// Global rate limit (200 req/min per IP)
app.use((req, res, next) => {
  const ip = getIp(req);
  if (!checkReqRate(ip)) {
    auditLog(ip, 'RATE_LIMITED', req.path);
    return res.status(429).json({ error: 'Too many requests' });
  }
  next();
});

// Basic Auth with brute-force protection
app.use((req, res, next) => {
  const ip = getIp(req);
  if (isAuthBlocked(ip)) {
    return res.status(429).send('Too many failed attempts. Try again in 30 minutes.');
  }
  const auth = req.headers['authorization'];
  if (auth && auth.startsWith('Basic ')) {
    const decoded = Buffer.from(auth.slice(6), 'base64').toString();
    const colon = decoded.indexOf(':');
    const user = decoded.slice(0, colon);
    const pass = decoded.slice(colon + 1);
    if (user === DASH_USER && pass === DASH_PASS) return next();
  }
  recordAuthFail(ip);
  res.set('WWW-Authenticate', 'Basic realm="WangLogistic Dashboard"');
  res.status(401).send('Unauthorized');
});

app.use(express.static(path.join(__dirname, 'public')));

// Ensure data directory exists
const dataDir = path.join(__dirname, '..', 'data');
if (!fs.existsSync(dataDir)) fs.mkdirSync(dataDir, { recursive: true });

const db = new Database(DB_PATH);
db.pragma('journal_mode = WAL');
db.pragma('foreign_keys = ON');

// Create tables if not exist
db.exec(`
  CREATE TABLE IF NOT EXISTS users (
    tg_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    created_at TEXT DEFAULT (datetime('now'))
  );

  CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    currency_from TEXT NOT NULL,
    currency_to TEXT NOT NULL,
    amount REAL NOT NULL,
    rate REAL,
    amount_result REAL,
    status TEXT DEFAULT 'new',
    manager_id INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(tg_id),
    FOREIGN KEY (manager_id) REFERENCES managers(tg_id)
  );

  CREATE TABLE IF NOT EXISTS managers (
    tg_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    is_active INTEGER DEFAULT 1,
    added_at TEXT DEFAULT (datetime('now'))
  );

  CREATE TABLE IF NOT EXISTS rates (
    pair TEXT PRIMARY KEY,
    buy_rate REAL NOT NULL,
    sell_rate REAL NOT NULL,
    updated_at TEXT DEFAULT (datetime('now'))
  );

  CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    sender_id INTEGER,
    text TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (order_id) REFERENCES orders(id)
  );

  CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
  );

  CREATE TABLE IF NOT EXISTS rate_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pair TEXT NOT NULL,
    buy_rate REAL NOT NULL,
    sell_rate REAL NOT NULL,
    changed_by INTEGER,
    source TEXT DEFAULT 'dashboard',
    created_at TEXT DEFAULT (datetime('now'))
  );
`);

// Migration: profit columns + banned_users
for (const col of ['usdt_amount REAL', 'cny_bought REAL', 'margin_cny REAL', 'margin_rub REAL']) {
  try { db.exec(`ALTER TABLE orders ADD COLUMN ${col}`); } catch (e) {}
}
db.exec(`CREATE TABLE IF NOT EXISTS banned_users (
  tg_id INTEGER PRIMARY KEY,
  reason TEXT,
  banned_at TEXT DEFAULT (datetime('now'))
);`);

// Migration: broadcast_chats
db.exec(`CREATE TABLE IF NOT EXISTS broadcast_chats (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_id TEXT UNIQUE NOT NULL,
  title TEXT,
  message_id TEXT,
  thread_id TEXT,
  auto_edit INTEGER DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now'))
);`);
try { db.exec('ALTER TABLE broadcast_chats ADD COLUMN thread_id TEXT'); } catch(e) {}

// Insert default rates if empty
const rateCount = db.prepare('SELECT COUNT(*) as cnt FROM rates').get();
if (rateCount.cnt === 0) {
  db.prepare('INSERT INTO rates (pair, buy_rate, sell_rate) VALUES (?, ?, ?)').run('RUB/CNY', 12.80, 13.20);
}

// ---- Broadcast helpers ----
function resolveBroadcastText(template) {
  const rate = db.prepare("SELECT * FROM rates WHERE pair = ?").get('RUB/CNY');
  const buyRate  = rate ? Math.round(parseFloat(rate.buy_rate)  * 100) / 100 : 0;
  const sellRate = rate ? Math.round(parseFloat(rate.sell_rate) * 100) / 100 : 0;
  let tiers = [];
  try { tiers = JSON.parse((db.prepare("SELECT value FROM settings WHERE key='volume_discounts'").get() || {}).value || '[]'); } catch(e) {}
  const tiersMap = {};
  tiers.forEach(t => { tiersMap[String(Math.floor(parseFloat(t.min_cny)))] = parseFloat(t.discount); });
  let banks = [];
  try { banks = JSON.parse((db.prepare("SELECT value FROM settings WHERE key='bank_discounts'").get() || {}).value || '[]'); } catch(e) {}
  const banksMap = {};
  banks.forEach(b => { banksMap[b.bank] = parseFloat(b.discount); });
  let text = template;
  text = text.replace(/\{тир:(\d+(?:\.\d+)?)\}/g, (_, n) => {
    const key = String(Math.floor(parseFloat(n)));
    const d = tiersMap[key];
    return d !== undefined ? String(Math.round((buyRate - d) * 100) / 100) : `{тир:${n}}`;
  });
  text = text.replace(/\{банк:([^}]+)\}/g, (_, bank) => {
    const d = banksMap[bank];
    return d !== undefined ? String(Math.round(d * 100) / 100) : `{банк:${bank}}`;
  });
  text = text.replace(/\{курс\}/g, String(buyRate));
  text = text.replace(/\{курс_продажи\}/g, String(sellRate));
  return text;
}

function tgRequest(method, params) {
  return new Promise((resolve, reject) => {
    const { bot_token } = readConfig();
    if (!bot_token) return reject(new Error('No bot token'));
    const postData = JSON.stringify(params);
    const options = {
      hostname: 'api.telegram.org',
      path: `/bot${bot_token}/${method}`,
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(postData) }
    };
    const req = https.request(options, (r) => {
      let data = '';
      r.on('data', d => data += d);
      r.on('end', () => { try { resolve(JSON.parse(data)); } catch(e) { resolve({ ok: false }); } });
    });
    req.on('error', reject);
    req.write(postData);
    req.end();
  });
}

async function autoBroadcast() {
  const template = (db.prepare("SELECT value FROM settings WHERE key='broadcast_template'").get() || {}).value || '';
  if (!template) return;
  const text = resolveBroadcastText(template);
  const chatsWithMsg = db.prepare('SELECT * FROM broadcast_chats WHERE auto_edit = 1 AND message_id IS NOT NULL').all();
  const chatsNew     = db.prepare('SELECT * FROM broadcast_chats WHERE auto_edit = 1 AND message_id IS NULL').all();
  for (const chat of chatsWithMsg) {
    const p = { chat_id: chat.chat_id, message_id: parseInt(chat.message_id), text, parse_mode: 'HTML', disable_web_page_preview: true };
    tgRequest('editMessageText', p).catch(() => {});
  }
  for (const chat of chatsNew) {
    const p = { chat_id: chat.chat_id, text, parse_mode: 'HTML', disable_web_page_preview: true };
    if (chat.thread_id) p.message_thread_id = parseInt(chat.thread_id);
    tgRequest('sendMessage', p)
      .then(r => { if (r.ok) db.prepare('UPDATE broadcast_chats SET message_id = ? WHERE id = ?').run(String(r.result.message_id), chat.id); })
      .catch(() => {});
  }
}

// ============ Helper: read/write config.py ============

function readConfig() {
  try {
    const content = fs.readFileSync(CONFIG_PATH, 'utf8');
    const m = content.match(/^BOT_TOKEN\s*=\s*["'](.+?)["']/m);
    return { bot_token: m ? m[1] : '' };
  } catch (e) {
    return { bot_token: '' };
  }
}

function writeConfig(bot_token) {
  const content = `BOT_TOKEN = "${bot_token}"
DB_PATH = "/root/projects/wanglogistic/data/wanglogistic.db"

DEFAULT_RATES = {
    "RUB/CNY": {"buy": 12.80, "sell": 13.20},
}
`;
  fs.writeFileSync(CONFIG_PATH, content, 'utf8');
}

function restartBot() {
  try {
    execSync('systemctl restart wanglogistic-bot', { timeout: 10000 });
    return true;
  } catch (e) {
    return false;
  }
}

function getBotStatus() {
  try {
    const output = execSync('systemctl is-active wanglogistic-bot', { timeout: 5000 }).toString().trim();
    return output;
  } catch (e) {
    return 'inactive';
  }
}

// ============ API: Stats ============

app.get('/api/stats', (req, res) => {
  try {
    const today = db.prepare(`
      SELECT COUNT(*) as count, COALESCE(SUM(amount), 0) as volume
      FROM orders WHERE date(created_at) = date('now')
    `).get();

    const week = db.prepare(`
      SELECT COUNT(*) as count, COALESCE(SUM(amount), 0) as volume
      FROM orders WHERE created_at >= datetime('now', '-7 days')
    `).get();

    const month = db.prepare(`
      SELECT COUNT(*) as count, COALESCE(SUM(amount), 0) as volume
      FROM orders WHERE created_at >= datetime('now', '-30 days')
    `).get();

    const active = db.prepare(`
      SELECT COUNT(*) as count FROM orders WHERE status IN ('new', 'taken', 'in_progress')
    `).get();

    const completed = db.prepare(`
      SELECT COUNT(*) as count FROM orders WHERE status = 'completed'
    `).get();

    const total = db.prepare(`SELECT COUNT(*) as count FROM orders`).get();

    const conversionRate = total.count > 0
      ? ((completed.count / total.count) * 100).toFixed(1)
      : '0.0';

    res.json({
      today: { orders: today.count, volume: today.volume },
      week: { orders: week.count, volume: week.volume },
      month: { orders: month.count, volume: month.volume },
      active: active.count,
      conversion_rate: parseFloat(conversionRate)
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ============ API: Orders ============

app.get('/api/orders', (req, res) => {
  try {
    let sql = `
      SELECT o.*, u.username as user_username, u.first_name as user_name,
             m.username as manager_username, m.first_name as manager_name
      FROM orders o
      LEFT JOIN users u ON o.user_id = u.tg_id
      LEFT JOIN managers m ON o.manager_id = m.tg_id
      WHERE 1=1
    `;
    const params = [];

    if (req.query.status) {
      sql += ' AND o.status = ?';
      params.push(req.query.status);
    }
    if (req.query.manager) {
      sql += ' AND o.manager_id = ?';
      params.push(req.query.manager);
    }
    if (req.query.from) {
      sql += ' AND o.created_at >= ?';
      params.push(req.query.from);
    }
    if (req.query.to) {
      sql += ' AND o.created_at <= ?';
      params.push(req.query.to + ' 23:59:59');
    }
    if (req.query.q) {
      const q = req.query.q.trim();
      const like = '%' + q + '%';
      if (/^\d+$/.test(q) && q.length <= 6) {
        sql += ` AND (o.id = ? OR u.first_name LIKE ? OR u.username LIKE ? OR m.first_name LIKE ? OR m.username LIKE ? OR o.id IN (SELECT DISTINCT order_id FROM messages WHERE text LIKE ?))`;
        params.push(parseInt(q), like, like, like, like, like);
      } else {
        sql += ` AND (u.first_name LIKE ? OR u.username LIKE ? OR m.first_name LIKE ? OR m.username LIKE ? OR o.id IN (SELECT DISTINCT order_id FROM messages WHERE text LIKE ?))`;
        params.push(like, like, like, like, like);
      }
    }

    sql += ' ORDER BY o.created_at DESC';

    const orders = db.prepare(sql).all(...params);
    res.json(orders);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/orders/:id', (req, res) => {
  try {
    const order = db.prepare(`
      SELECT o.*, u.username as user_username, u.first_name as user_name,
             m.username as manager_username, m.first_name as manager_name
      FROM orders o
      LEFT JOIN users u ON o.user_id = u.tg_id
      LEFT JOIN managers m ON o.manager_id = m.tg_id
      WHERE o.id = ?
    `).get(req.params.id);

    if (!order) return res.status(404).json({ error: 'Order not found' });

    const messages = db.prepare(`
      SELECT msg.*, u.username as sender_username, u.first_name as sender_name
      FROM messages msg
      LEFT JOIN users u ON msg.sender_id = u.tg_id
      WHERE msg.order_id = ?
      ORDER BY msg.created_at ASC
    `).all(req.params.id);

    res.json({ ...order, messages });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.put('/api/orders/:id', (req, res) => {
  try {
    const { status, manager_id } = req.body;
    const sets = [];
    const params = [];

    if (status) { sets.push('status = ?'); params.push(status); }
    if (manager_id !== undefined) { sets.push('manager_id = ?'); params.push(manager_id); }
    sets.push("updated_at = datetime('now')");

    params.push(req.params.id);
    db.prepare(`UPDATE orders SET ${sets.join(', ')} WHERE id = ?`).run(...params);

    const order = db.prepare('SELECT * FROM orders WHERE id = ?').get(req.params.id);
    res.json(order);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ============ API: Profile / QR proxy ============

app.get('/api/profile/:tg_id', (req, res) => {
  try {
    const profile = db.prepare('SELECT wechat_qr, alipay_qr FROM profiles WHERE tg_id = ?').get(req.params.tg_id);
    res.json(profile || {});
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/tg-file', (req, res) => {
  const { file_id } = req.query;
  if (!file_id) return res.status(400).end();
  const { bot_token } = readConfig();
  if (!bot_token) return res.status(500).json({ error: 'No bot token' });

  https.get(`https://api.telegram.org/bot${bot_token}/getFile?file_id=${file_id}`, (r) => {
    let data = '';
    r.on('data', d => data += d);
    r.on('end', () => {
      try {
        const json = JSON.parse(data);
        if (!json.ok) return res.status(404).end();
        const file_path = json.result.file_path;
        const imgUrl = `https://api.telegram.org/file/bot${bot_token}/${file_path}`;
        https.get(imgUrl, (imgRes) => {
          res.setHeader('Content-Type', imgRes.headers['content-type'] || 'image/jpeg');
          imgRes.pipe(res);
        }).on('error', () => res.status(500).end());
      } catch (e) {
        res.status(500).end();
      }
    });
  }).on('error', () => res.status(500).end());
});

// ============ API: Managers ============

app.get('/api/managers', (req, res) => {
  try {
    const { from, to } = req.query;
    const dateFilter = from && to
      ? `AND date(updated_at) BETWEEN '${from}' AND '${to}'`
      : '';
    const managers = db.prepare(`
      SELECT m.*,
        (SELECT COUNT(*) FROM orders WHERE manager_id = m.tg_id) as total_orders,
        (SELECT COUNT(*) FROM orders WHERE manager_id = m.tg_id AND status = 'completed') as completed_orders,
        (SELECT ROUND(AVG(
          (julianday(updated_at) - julianday(created_at)) * 24 * 60
        ), 0) FROM orders WHERE manager_id = m.tg_id AND status = 'completed') as avg_time_minutes,
        (SELECT ROUND(COALESCE(SUM(margin_rub), 0), 2)
         FROM orders WHERE manager_id = m.tg_id AND status = 'completed' AND margin_rub IS NOT NULL
        ) as total_profit,
        (SELECT COUNT(*) FROM orders WHERE manager_id = m.tg_id AND status = 'completed' ${dateFilter}) as period_orders,
        (SELECT ROUND(COALESCE(SUM(margin_rub), 0), 2)
         FROM orders WHERE manager_id = m.tg_id AND status = 'completed' AND margin_rub IS NOT NULL ${dateFilter}
        ) as period_profit
      FROM managers m
      ORDER BY m.added_at DESC
    `).all();
    res.json(managers);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.post('/api/managers', (req, res) => {
  try {
    const { tg_id, username, first_name } = req.body;
    if (!tg_id) return res.status(400).json({ error: 'tg_id required' });

    db.prepare(`
      INSERT OR REPLACE INTO managers (tg_id, username, first_name, is_active)
      VALUES (?, ?, ?, 1)
    `).run(tg_id, username || null, first_name || null);
    auditLog(getIp(req), 'MANAGER_ADD', `tg_id=${tg_id} username=${username}`);

    const manager = db.prepare('SELECT * FROM managers WHERE tg_id = ?').get(tg_id);
    res.json(manager);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.put('/api/managers/:id', (req, res) => {
  try {
    const { username, first_name, is_active } = req.body;
    const sets = [];
    const params = [];

    if (username !== undefined) { sets.push('username = ?'); params.push(username); }
    if (first_name !== undefined) { sets.push('first_name = ?'); params.push(first_name); }
    if (is_active !== undefined) { sets.push('is_active = ?'); params.push(is_active ? 1 : 0); }

    if (sets.length === 0) return res.status(400).json({ error: 'Nothing to update' });

    params.push(req.params.id);
    db.prepare(`UPDATE managers SET ${sets.join(', ')} WHERE tg_id = ?`).run(...params);

    const manager = db.prepare('SELECT * FROM managers WHERE tg_id = ?').get(req.params.id);
    res.json(manager);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.delete('/api/managers/:id', (req, res) => {
  try {
    db.prepare('DELETE FROM managers WHERE tg_id = ?').run(req.params.id);
    auditLog(getIp(req), 'MANAGER_DELETE', `tg_id=${req.params.id}`);
    res.json({ ok: true });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/managers/:id/orders', (req, res) => {
  try {
    const orders = db.prepare(`
      SELECT o.id, o.amount, o.currency_from, o.currency_to,
             o.amount_result, o.rate, o.offer_rate, o.htx_rate,
             o.usdt_amount, o.cny_bought, o.margin_cny,
             o.status, o.created_at, o.updated_at,
             u.first_name as user_name, u.username as user_username
      FROM orders o
      LEFT JOIN users u ON o.user_id = u.tg_id
      WHERE o.manager_id = ?
      ORDER BY o.created_at DESC
    `).all(req.params.id);

    const stats = db.prepare(`
      SELECT
        COUNT(*) as total_orders,
        SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed_orders,
        ROUND(COALESCE(SUM(CASE WHEN status = 'completed' THEN margin_rub END), 0), 2) as total_profit
      FROM orders WHERE manager_id = ?
    `).get(req.params.id);

    res.json({ orders, stats });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ============ API: Users ============

app.get('/api/users/search', (req, res) => {
  try {
    const q = (req.query.q || '').trim().slice(0, 50);
    if (!q) return res.json([]);
    if (!/^[\w@.\- ]+$/i.test(q)) return res.json([]);
    const like = '%' + q + '%';
    const users = db.prepare(`
      SELECT u.tg_id, u.username, u.first_name, u.created_at,
             (SELECT COUNT(*) FROM orders WHERE user_id = u.tg_id) as orders_count,
             (b.tg_id IS NOT NULL) as is_banned,
             b.reason as ban_reason
      FROM users u
      LEFT JOIN banned_users b ON u.tg_id = b.tg_id
      WHERE u.username LIKE ? OR u.first_name LIKE ? OR CAST(u.tg_id AS TEXT) LIKE ?
      ORDER BY u.created_at DESC LIMIT 30
    `).all(like, like, like);
    res.json(users);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/users/banned', (req, res) => {
  try {
    const users = db.prepare(`
      SELECT b.tg_id, b.reason, b.banned_at, u.username, u.first_name
      FROM banned_users b
      LEFT JOIN users u ON b.tg_id = u.tg_id
      ORDER BY b.banned_at DESC
    `).all();
    res.json(users);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.post('/api/users/:id/ban', (req, res) => {
  try {
    const tg_id = parseInt(req.params.id);
    const reason = req.body.reason || '';
    db.prepare('INSERT OR REPLACE INTO banned_users (tg_id, reason) VALUES (?, ?)').run(tg_id, reason);
    auditLog(getIp(req), 'BAN_USER', `tg_id=${tg_id} reason=${reason}`);

    // Notify user via bot
    const { bot_token } = readConfig();
    const mainManager = (db.prepare("SELECT value FROM settings WHERE key = 'main_manager'").get() || {}).value || '';
    if (bot_token && tg_id) {
      const reasonLine = reason ? `\nПричина: <b>${reason}</b>` : '';
      const managerLine = mainManager ? `\n\nЕсли считаете, что это ошибка — напишите главному менеджеру: @${mainManager}` : '';
      const text = `🚫 Вы были заблокированы в боте WangLogistic.${reasonLine}${managerLine}`;
      const postData = JSON.stringify({ chat_id: tg_id, text, parse_mode: 'HTML' });
      const options = {
        hostname: 'api.telegram.org',
        path: `/bot${bot_token}/sendMessage`,
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(postData) }
      };
      const tgReq = https.request(options);
      tgReq.on('error', () => {});
      tgReq.write(postData);
      tgReq.end();
    }

    res.json({ ok: true });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.delete('/api/users/:id/ban', (req, res) => {
  try {
    const tg_id_unban = parseInt(req.params.id);
    db.prepare('DELETE FROM banned_users WHERE tg_id = ?').run(tg_id_unban);
    auditLog(getIp(req), 'UNBAN_USER', `tg_id=${tg_id_unban}`);
    res.json({ ok: true });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ============ API: Rates ============

app.get('/api/rates', (req, res) => {
  try {
    const rates = db.prepare('SELECT * FROM rates ORDER BY pair').all();
    res.json(rates);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/rates/log', (req, res) => {
  try {
    const pair = req.query.pair || 'RUB/CNY';
    const limit = parseInt(req.query.limit) || 20;
    const logs = db.prepare(`
      SELECT l.*,
        COALESCE(m.first_name, u.first_name) as first_name,
        COALESCE(m.username, u.username) as username
      FROM rate_log l
      LEFT JOIN managers m ON l.changed_by = m.tg_id
      LEFT JOIN users u ON l.changed_by = u.tg_id
      WHERE l.pair = ?
      ORDER BY l.created_at DESC LIMIT ?
    `).all(pair, limit);
    res.json(logs);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.put('/api/rates', (req, res) => {
  try {
    const { pair, buy_rate, sell_rate } = req.body;
    if (!pair) return res.status(400).json({ error: 'pair required' });

    db.prepare(`
      INSERT INTO rates (pair, buy_rate, sell_rate, updated_at)
      VALUES (?, ?, ?, datetime('now'))
      ON CONFLICT(pair) DO UPDATE SET
        buy_rate = excluded.buy_rate,
        sell_rate = excluded.sell_rate,
        updated_at = datetime('now')
    `).run(pair, buy_rate, sell_rate);

    db.prepare(
      'INSERT INTO rate_log (pair, buy_rate, sell_rate, source) VALUES (?, ?, ?, ?)'
    ).run(pair, buy_rate, sell_rate, 'dashboard');

    auditLog(getIp(req), 'RATES_CHANGE', `pair=${pair} buy=${buy_rate} sell=${sell_rate}`);
    autoBroadcast().catch(() => {});
    const rate = db.prepare('SELECT * FROM rates WHERE pair = ?').get(pair);
    res.json(rate);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ============ API: Settings + Bot control ============

app.get('/api/settings', (req, res) => {
  try {
    const config = readConfig();
    const getSetting = (key, def) => {
      const row = db.prepare("SELECT value FROM settings WHERE key = ?").get(key);
      return row ? row.value : def;
    };
    res.json({
      bot_token: config.bot_token,
      receipt_guide_url: getSetting('receipt_guide_url', 'https://telegra.ph/test-cheki-03-23'),
      main_manager: getSetting('main_manager', 'bulievich'),
      rules_url: getSetting('rules_url', 'https://telegra.ph/Pravila-ispolzovaniya-servisa-WangLogistic-03-23'),
      promotions_text: getSetting('promotions_text', ''),
      volume_discounts: getSetting('volume_discounts', '[]'),
      min_buy_amount: getSetting('min_buy_amount', '0'),
      min_sell_amount: getSetting('min_sell_amount', '0'),
      bank_discounts: getSetting('bank_discounts', '[]'),
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.put('/api/settings', (req, res) => {
  try {
    const { bot_token, receipt_guide_url } = req.body;
    let needRestart = false;

    if (bot_token) {
      const current = readConfig();
      if (bot_token !== current.bot_token) {
        writeConfig(bot_token);
        needRestart = true;
        restartBot();
      }
    }

    const upsertSetting = (key, value) => {
      if (value !== undefined) {
        db.prepare("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value").run(key, value);
      }
    };
    upsertSetting('receipt_guide_url', receipt_guide_url);
    upsertSetting('main_manager', req.body.main_manager);
    upsertSetting('rules_url', req.body.rules_url);
    upsertSetting('promotions_text', req.body.promotions_text);
    if (req.body.volume_discounts !== undefined) {
      try { JSON.parse(req.body.volume_discounts); } catch(e) { return res.status(400).json({ error: 'Невалидный JSON в тирах скидок' }); }
      upsertSetting('volume_discounts', req.body.volume_discounts);
    }
    if (req.body.min_buy_amount !== undefined) upsertSetting('min_buy_amount', req.body.min_buy_amount);
    if (req.body.min_sell_amount !== undefined) upsertSetting('min_sell_amount', req.body.min_sell_amount);
    if (req.body.bank_discounts !== undefined) {
      try { JSON.parse(req.body.bank_discounts); } catch(e) { return res.status(400).json({ error: 'Невалидный JSON в скидках банков' }); }
      upsertSetting('bank_discounts', req.body.bank_discounts);
    }

    auditLog(getIp(req), 'SETTINGS_CHANGE', Object.keys(req.body).filter(k => k !== 'bot_token').join(','));
    res.json({ ok: true, restarted: needRestart });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Bot status & control
app.get('/api/bot/status', (req, res) => {
  try {
    const status = getBotStatus();
    let uptime = '';
    try {
      uptime = execSync('systemctl show wanglogistic-bot --property=ActiveEnterTimestamp --value', { timeout: 5000 }).toString().trim();
    } catch (e) {}
    res.json({ status, uptime });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.post('/api/bot/restart', (req, res) => {
  try {
    const ok = restartBot();
    const status = getBotStatus();
    auditLog(getIp(req), 'BOT_RESTART');
    res.json({ ok, status });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ============ API: Broadcast ============

app.get('/api/broadcast/chats', (req, res) => {
  try { res.json(db.prepare('SELECT * FROM broadcast_chats ORDER BY created_at DESC').all()); }
  catch (err) { res.status(500).json({ error: err.message }); }
});

app.post('/api/broadcast/chats', (req, res) => {
  try {
    const { chat_id, title, thread_id } = req.body;
    if (!chat_id) return res.status(400).json({ error: 'chat_id required' });
    db.prepare('INSERT OR REPLACE INTO broadcast_chats (chat_id, title, thread_id) VALUES (?, ?, ?)').run(String(chat_id), title || String(chat_id), thread_id ? String(thread_id) : null);
    auditLog(getIp(req), 'BROADCAST_CHAT_ADD', `chat_id=${chat_id}`);
    res.json(db.prepare('SELECT * FROM broadcast_chats WHERE chat_id = ?').get(String(chat_id)));
  } catch (err) { res.status(500).json({ error: err.message }); }
});

app.put('/api/broadcast/chats/:id', (req, res) => {
  try {
    const { auto_edit, title, message_id, thread_id } = req.body;
    const sets = []; const params = [];
    if (title      !== undefined) { sets.push('title = ?');      params.push(title); }
    if (message_id !== undefined) { sets.push('message_id = ?'); params.push(message_id); }
    if (thread_id  !== undefined) { sets.push('thread_id = ?');  params.push(thread_id || null); }
    if (auto_edit  !== undefined) { sets.push('auto_edit = ?');  params.push(auto_edit ? 1 : 0); }
    if (!sets.length) return res.status(400).json({ error: 'nothing to update' });
    params.push(req.params.id);
    db.prepare(`UPDATE broadcast_chats SET ${sets.join(', ')} WHERE id = ?`).run(...params);
    res.json(db.prepare('SELECT * FROM broadcast_chats WHERE id = ?').get(req.params.id));
  } catch (err) { res.status(500).json({ error: err.message }); }
});

app.delete('/api/broadcast/chats/:id', (req, res) => {
  try {
    db.prepare('DELETE FROM broadcast_chats WHERE id = ?').run(req.params.id);
    auditLog(getIp(req), 'BROADCAST_CHAT_DELETE', `id=${req.params.id}`);
    res.json({ ok: true });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

app.get('/api/broadcast/template', (req, res) => {
  try {
    const g = k => (db.prepare("SELECT value FROM settings WHERE key = ?").get(k) || {}).value;
    res.json({ template: g('broadcast_template') || '', auto_send: g('broadcast_auto_send') === '1' });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

app.put('/api/broadcast/template', (req, res) => {
  try {
    const up = (k, v) => db.prepare("INSERT INTO settings (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value").run(k, v);
    const { template, auto_send } = req.body;
    if (template   !== undefined) up('broadcast_template', template);
    if (auto_send  !== undefined) up('broadcast_auto_send', auto_send ? '1' : '0');
    auditLog(getIp(req), 'BROADCAST_TEMPLATE_SAVE');
    res.json({ ok: true });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

app.post('/api/broadcast/send', async (req, res) => {
  try {
    const template = (db.prepare("SELECT value FROM settings WHERE key='broadcast_template'").get() || {}).value || '';
    if (!template) return res.status(400).json({ error: 'Нет шаблона сообщения' });
    const text = resolveBroadcastText(template);
    const ids = req.body.chat_ids;
    const chats = ids && ids.length
      ? db.prepare(`SELECT * FROM broadcast_chats WHERE id IN (${ids.map(() => '?').join(',')})`).all(...ids)
      : db.prepare('SELECT * FROM broadcast_chats').all();
    const results = [];
    for (const chat of chats) {
      try {
        const p = { chat_id: chat.chat_id, text, parse_mode: 'HTML', disable_web_page_preview: true };
        if (chat.thread_id) p.message_thread_id = parseInt(chat.thread_id);
        const r = await tgRequest('sendMessage', p);
        if (r.ok) {
          const msgId = String(r.result.message_id);
          db.prepare('UPDATE broadcast_chats SET message_id = ? WHERE id = ?').run(msgId, chat.id);
          results.push({ id: chat.id, chat_id: chat.chat_id, ok: true, message_id: msgId });
        } else {
          results.push({ id: chat.id, chat_id: chat.chat_id, ok: false, error: r.description });
        }
      } catch(e) { results.push({ id: chat.id, chat_id: chat.chat_id, ok: false, error: e.message }); }
    }
    auditLog(getIp(req), 'BROADCAST_SEND', `chats=${chats.length}`);
    res.json({ results, text });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

app.post('/api/broadcast/edit', async (req, res) => {
  try {
    const template = (db.prepare("SELECT value FROM settings WHERE key='broadcast_template'").get() || {}).value || '';
    if (!template) return res.status(400).json({ error: 'Нет шаблона сообщения' });
    const text = resolveBroadcastText(template);
    const ids = req.body.chat_ids;
    const chats = ids && ids.length
      ? db.prepare(`SELECT * FROM broadcast_chats WHERE id IN (${ids.map(() => '?').join(',')})`).all(...ids)
      : db.prepare('SELECT * FROM broadcast_chats WHERE message_id IS NOT NULL').all();
    const results = [];
    for (const chat of chats) {
      if (!chat.message_id) { results.push({ id: chat.id, ok: false, error: 'No message_id' }); continue; }
      try {
        const r = await tgRequest('editMessageText', { chat_id: chat.chat_id, message_id: parseInt(chat.message_id), text, parse_mode: 'HTML', disable_web_page_preview: true });
        results.push({ id: chat.id, chat_id: chat.chat_id, ok: !!r.ok, error: r.description });
      } catch(e) { results.push({ id: chat.id, chat_id: chat.chat_id, ok: false, error: e.message }); }
    }
    auditLog(getIp(req), 'BROADCAST_EDIT', `chats=${chats.length}`);
    res.json({ results, text });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

// SPA fallback
app.get('*', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

app.listen(PORT, () => {
  console.log(`WangLogistic Dashboard running on http://localhost:${PORT}`);
});
