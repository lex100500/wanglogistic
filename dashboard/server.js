const express = require('express');
const Database = require('better-sqlite3');
const path = require('path');

const app = express();
const PORT = 3001;
const DB_PATH = path.join(__dirname, '..', 'data', 'wanglogistic.db');

app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

// Ensure data directory exists
const fs = require('fs');
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
`);

// Insert default rates if empty
const rateCount = db.prepare('SELECT COUNT(*) as cnt FROM rates').get();
if (rateCount.cnt === 0) {
  const insertRate = db.prepare('INSERT INTO rates (pair, buy_rate, sell_rate) VALUES (?, ?, ?)');
  insertRate.run('USD/RUB', 92.50, 94.00);
  insertRate.run('EUR/RUB', 100.50, 102.00);
  insertRate.run('USDT/RUB', 92.00, 93.50);
  insertRate.run('CNY/RUB', 12.80, 13.20);
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

// ============ API: Managers ============

app.get('/api/managers', (req, res) => {
  try {
    const managers = db.prepare(`
      SELECT m.*,
        (SELECT COUNT(*) FROM orders WHERE manager_id = m.tg_id) as total_orders,
        (SELECT COUNT(*) FROM orders WHERE manager_id = m.tg_id AND status = 'completed') as completed_orders,
        (SELECT ROUND(AVG(
          (julianday(updated_at) - julianday(created_at)) * 24 * 60
        ), 0) FROM orders WHERE manager_id = m.tg_id AND status = 'completed') as avg_time_minutes
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

    const manager = db.prepare('SELECT * FROM managers WHERE tg_id = ?').get(tg_id);
    res.json(manager);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.delete('/api/managers/:id', (req, res) => {
  try {
    db.prepare('DELETE FROM managers WHERE tg_id = ?').run(req.params.id);
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

app.put('/api/rates/:pair', (req, res) => {
  try {
    const pair = decodeURIComponent(req.params.pair);
    const { buy_rate, sell_rate } = req.body;

    db.prepare(`
      INSERT INTO rates (pair, buy_rate, sell_rate, updated_at)
      VALUES (?, ?, ?, datetime('now'))
      ON CONFLICT(pair) DO UPDATE SET
        buy_rate = excluded.buy_rate,
        sell_rate = excluded.sell_rate,
        updated_at = datetime('now')
    `).run(pair, buy_rate, sell_rate);

    const rate = db.prepare('SELECT * FROM rates WHERE pair = ?').get(pair);
    res.json(rate);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ============ API: Settings ============

app.get('/api/settings', (req, res) => {
  try {
    const rows = db.prepare('SELECT * FROM settings').all();
    const settings = {};
    rows.forEach(r => { settings[r.key] = r.value; });
    res.json(settings);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.put('/api/settings', (req, res) => {
  try {
    const upsert = db.prepare(`
      INSERT INTO settings (key, value) VALUES (?, ?)
      ON CONFLICT(key) DO UPDATE SET value = excluded.value
    `);
    const tx = db.transaction((entries) => {
      for (const [key, value] of entries) {
        upsert.run(key, value);
      }
    });
    tx(Object.entries(req.body));
    res.json({ ok: true });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// SPA fallback
app.get('*', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

app.listen(PORT, () => {
  console.log(`WangLogistic Dashboard running on http://localhost:${PORT}`);
});
