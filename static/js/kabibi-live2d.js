/* ── 卡比比酱 · 伪 Live2D 看板娘 ────────────────────────────
 * 单张立绘的 mesh 形变动画：呼吸、上身摆动、头发/裙摆滞后飘动、
 * 头部随鼠标转动、随机眨眼；点她会弹跳一下。
 *
 * 实现：把立绘切成 N 条横切片，每片用一个带 shear 的仿射矩阵画出去
 * （上缘和下缘各自对位），切片之间不会错台；纵向映射按行积分，
 * 呼吸拉伸时也不露缝。素材由 scripts/make_kabibi_assets.py 生成，
 * 所有计算都在立绘像素空间里做，最后按 dpr 缩放绘制。
 * ─────────────────────────────────────────────────────────── */
(function () {
  'use strict';

  var DIR = '/static/images/kabibi/';
  var BANDS = 108;     // 横向切片数：越多形变越平滑
  var PADX = 46;       // 立绘两侧余量（立绘像素），头转过去不会被画布裁掉
  var AMP = {
    sway: 30,          // 上身绕腰摆动（立绘像素）
    lean: 0.035,       // 头部随视线的侧倾（弧度）
    gazeX: 34,         // 视线跟随带来的头部平移
    nodY: 9,           // 视线跟随带来的点头
    hair: 16,          // 发梢滞后摆动
    skirt: 12,         // 裙摆滞后摆动
    breath: 0.02,      // 呼吸的纵向拉伸比例
    react: 0.055       // 点击弹跳的挤压比例
  };

  function clamp(v, a, b) { return v < a ? a : (v > b ? b : v); }

  function smoothstep(a, b, x) {
    var t = clamp((x - a) / (b - a), 0, 1);
    return t * t * (3 - 2 * t);
  }

  // 在 mid 处到顶、向 lo / hi 两侧平滑落回 0 的山包权重
  function bump(lo, mid, hi, y) {
    return y < mid ? smoothstep(lo, mid, y) : 1 - smoothstep(mid, hi, y);
  }

  function easeInOut(t) { return t * t * (3 - 2 * t); }

  function loadImage(src) {
    return new Promise(function (resolve, reject) {
      var img = new Image();
      img.onload = function () { resolve(img); };
      img.onerror = function () { reject(new Error('图片加载失败 ' + src)); };
      img.src = src;
    });
  }

  function Kabibi(root) {
    var self = this;
    this.root = root;
    this.t = Math.random() * 12;        // 随机相位，别和页面其它动画同频
    this.gx = 0; this.gy = 0;           // 平滑后的视线
    this.tgx = 0; this.tgy = 0;         // 视线目标（鼠标 / 空闲游走）
    this.pointerAt = -1e9;
    this.reactT = 1e9;                  // 点击弹跳计时
    this.blinks = [];                   // 进行中的眨眼 [起始时刻, 时长]
    this.blink = 0;
    this.breath = 0; this.squash = 0; this.nod = 0;
    this.mapY = new Float32Array(BANDS + 1);   // 切片边界的纵向映射（立绘像素）
    this.offE = new Float32Array(BANDS + 1);   // 切片边界的横向偏移（立绘像素）
    this.sclM = new Float32Array(BANDS);       // 切片中点的横向缩放
    this.k = 0;
    this.dpr = Math.min(2, window.devicePixelRatio || 1);
    this.visible = true;
    this.running = false;
    this.drewStatic = false;
    this.static = !!(window.matchMedia &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    this.blinkAt = this.static ? 1e9 : this.t + 1.2;
    this.offFn = function () { return 0; };
    this.loop = function () {
      if (self.stopped || !self.visible) { self.running = false; return; }
      if (self.static) {                        // 减少动效：画一帧静帧就停掉循环
        self.drewStatic = true;
        self.step(0);
        self.running = false;
        return;
      }
      var now = performance.now() / 1000;
      var dt = self.last ? Math.min(0.05, now - self.last) : 0.016;
      self.last = now;
      self.step(self.static ? 0 : dt);
      self.raf = window.requestAnimationFrame(self.loop);
    };
  }

  Kabibi.prototype.load = function () {
    var self = this;
    return fetch(DIR + 'manifest.json', { cache: 'no-cache' })
      .then(function (r) {
        if (!r.ok) throw new Error('manifest ' + r.status);
        return r.json();
      })
      .then(function (m) {
        self.m = m;
        var names = [m.src];
        m.eyes.forEach(function (e) { names.push(e.lid.src, e.line.src); });
        return Promise.all(names.map(function (n) { return loadImage(DIR + n); }));
      })
      .then(function (imgs) {
        self.img = { base: imgs[0] };
        self.m.eyes.forEach(function (e, i) {
          self.img['lid' + i] = imgs[1 + i * 2];
          self.img['line' + i] = imgs[2 + i * 2];
        });
      });
  };

  /* 按容器高度确定画布尺寸；左右各多留 PADX 余量给头部转动。 */
  Kabibi.prototype.resize = function () {
    var h = this.root.clientHeight;
    if (!h || !this.m) return;
    this.dpr = Math.min(2, window.devicePixelRatio || 1);
    this.k = (h / this.m.h) * this.dpr;
    var cw = Math.round((this.m.w + 2 * PADX) * this.k);
    var ch = Math.round(this.m.h * this.k);
    this.canvas.style.width = (cw / this.dpr) + 'px';
    this.canvas.style.height = (ch / this.dpr) + 'px';
    this.canvas.width = cw;
    this.canvas.height = ch;
    if (this.static) this.drewStatic = false;    // 尺寸变了要重画静帧
  };

  /* 横向偏移取切片边界值（边界对边界才不会错台）；
   * 纵向按行积分呼吸拉伸，最后把脚底对齐回原位。 */
  Kabibi.prototype.computeMap = function () {
    var A = this.m.anchors, H = this.m.h, i;
    var bh = H / BANDS, y = 0, acc = 0;
    for (i = 0; i < BANDS; i++) {
      var ym = y + bh * 0.5;
      var u = clamp((A.feet - ym) / (A.feet - A.headTop), 0, 1);
      var sy = 1 + this.breath * AMP.breath * Math.pow(u, 0.85)
                 - this.squash * AMP.react * 1.6;
      this.sclM[i] = 1 - 0.3 * (sy - 1);
      this.mapY[i] = acc;
      this.offE[i] = this.offFn(y);
      acc += bh * sy;
      y += bh;
    }
    this.mapY[BANDS] = acc;
    this.offE[BANDS] = this.offFn(y);
    var lift = A.feet - acc;                       // 脚底固定
    for (i = 0; i <= BANDS; i++) {
      var yy = i * bh;
      this.mapY[i] += lift + this.nod * (1 - smoothstep(A.neck, A.shoulder + 110, yy));
    }
  };

  // 立绘坐标 y 处的横向偏移（切片边界之间线性插值）
  Kabibi.prototype.offAt = function (y) {
    var bh = this.m.h / BANDS, i = clamp(Math.floor(y / bh), 0, BANDS - 1);
    var f = clamp((y - i * bh) / bh, 0, 1);
    return this.offE[i] + (this.offE[i + 1] - this.offE[i]) * f;
  };

  // 反解：画面纵向位置 yc（立绘坐标）对应立绘原图的哪一行
  Kabibi.prototype.unmap = function (yc) {
    var bh = this.m.h / BANDS, my = this.mapY;
    var i = clamp(Math.floor(yc / bh), 0, BANDS - 1);
    for (var g = 0; g <= BANDS; g++) {
      if (yc >= my[i] && yc < my[i + 1]) {
        return (i + (yc - my[i]) / (my[i + 1] - my[i])) * bh;
      }
      if (yc < my[i]) { if (i === 0) return 0; i--; }
      else { if (i === BANDS - 1) return this.m.h; i++; }
    }
    return clamp(yc, 0, this.m.h);
  };

  /* 画源行 yStart..yEnd 的内容（贴图贴在立绘坐标 ox / oy 处）。
   * 逐目标设备行绘制：每个设备行从源图取等高的一条，采样节奏一致，
   * 不会像整块切片缩放那样在切片之间留下明暗横纹。 */
  Kabibi.prototype.drawRows = function (img, ox, oy, yStart, yEnd, alpha) {
    var ctx = this.ctx, m = this.m, axis = m.anchors.axisX, k = this.k;
    if (!k || yEnd <= yStart) return;
    var top = Math.max(0, yStart - oy), bot = Math.min(img.height, yEnd - oy);
    var foot = 1 / k;                                  // 一个设备行占的源行高
    var y0 = Math.floor(yStart * k), y1 = Math.ceil(yEnd * k);
    if (alpha !== undefined) ctx.globalAlpha = alpha;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    for (var y = y0; y < y1; y++) {
      var yc = (y + 0.5) / k;                          // 该设备行的中心（立绘坐标）
      var ys = this.unmap(yc);
      if (ys < yStart || ys > yEnd) continue;
      var sc = this.sclM[clamp(Math.floor(ys / (m.h / BANDS)), 0, BANDS - 1)];
      var sy = ys - oy - foot * 0.5, sh = foot;
      if (sy < top) { sh -= top - sy; sy = top; }
      if (sy + sh > bot) sh = bot - sy;
      if (sh <= 0) continue;
      ctx.drawImage(
        img, 0, sy, img.width, sh,
        PADX * k + axis * k + (ox - axis) * sc * k + this.offAt(ys) * k,
        y, img.width * sc * k, 1
      );
    }
    ctx.globalAlpha = 1;
  };

  Kabibi.prototype.draw = function () {
    var ctx = this.ctx, m = this.m;
    if (!this.k) return;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);

    this.drawRows(this.img.base, 0, 0, 0, m.h);

    // 眨眼：眼皮自上而下压住眼睛，睫毛弧线贴着眼皮下缘
    var p = this.blink;
    if (p > 0.01) {
      for (var i = 0; i < m.eyes.length; i++) {
        var e = m.eyes[i], lid = e.lid, ln = e.line;
        var clip = e.top + p * (e.bottom - e.top);
        this.drawRows(this.img['lid' + i], lid.x, lid.y, lid.y, clip);
        var dy = (e.top + p * (ln.arcC - e.top)) - ln.arcC;
        this.drawRows(
          this.img['line' + i], ln.x, ln.y + dy, ln.y + dy,
          Math.min(ln.y + dy + ln.h, clip), Math.min(1, p * 6)
        );
      }
    }
    ctx.setTransform(1, 0, 0, 1, 0, 0);
  };

  /* 推进一帧姿态：呼吸、摆动、视线游走、眨眼调度、点击弹跳 */
  Kabibi.prototype.step = function (dt) {
    this.t += dt;
    var t = this.t, A = this.m.anchors;

    // 鼠标 4 秒不动就自己慢慢东张西望
    var idle = clamp((t - this.pointerAt - 4) / 4, 0, 1);
    var ix = 0.55 * Math.sin(t * 0.48) + 0.45 * Math.sin(t * 0.71 + 2.4);
    var iy = 0.5 * Math.sin(t * 0.37 + 1.1) + 0.5 * Math.sin(t * 0.63 + 0.4);
    this.tgx = this.tgx * (1 - idle) + idle * 0.55 * ix;
    this.tgy = this.tgy * (1 - idle) + idle * 0.5 * iy;
    var ease = 1 - Math.exp(-dt * 2.6);
    this.gx += (this.tgx - this.gx) * ease;
    this.gy += (this.tgy - this.gy) * ease;

    this.reactT += dt;
    var r = this.reactT < 1.4 ? Math.exp(-3.1 * this.reactT) * Math.sin(11 * this.reactT) : 0;

    if (t >= this.blinkAt) {
      var dur = 0.14 + Math.random() * 0.06;
      this.blinks.push([t, dur]);
      if (Math.random() < 0.18) this.blinks.push([t + dur + 0.09, dur * 0.8]);
      var gap = Math.random() < 0.12 ? 0.7 + Math.random() * 0.8 : 2.2 + Math.random() * 4.2;
      this.blinkAt = t + gap;
    }
    var blink = 0;
    for (var i = this.blinks.length - 1; i >= 0; i--) {
      var b = this.blinks[i], el = t - b[0];
      if (el < 0) continue;
      var close = b[1] * 0.42, hold = b[1] * 0.1, open = b[1] * 0.48, v;
      if (el < close) v = easeInOut(el / close);
      else if (el < close + hold) v = 1;
      else if (el < close + hold + open) v = 1 - easeInOut((el - close - hold) / open);
      else { this.blinks.splice(i, 1); continue; }
      blink = Math.max(blink, v);
    }

    var breath = Math.sin(t * 1.36);                                // ≈4.6s 一次呼吸
    var swayV = 0.62 * Math.sin(t * 0.94) + 0.38 * Math.sin(t * 0.61 + 1.9);
    var lag = 0.45;                                                 // 头发慢半拍
    var hairV = 0.62 * Math.sin((t - lag) * 0.94) + 0.38 * Math.sin((t - lag) * 0.61 + 1.9);
    var skirtV = Math.sin((t - 0.3) * 0.77 + 0.8);
    var gx = this.gx;

    this.breath = breath;
    this.squash = r;
    this.nod = this.gy * AMP.nodY;
    this.blink = blink;

    var headW = function (y) { return 1 - smoothstep(A.neck, A.shoulder + 110, y); };
    var bend = function (y) {
      if (y < A.waist) return (A.waist - y) / (A.waist - A.headTop);
      return -0.15 * Math.sin(Math.PI * clamp((y - A.waist) / (A.feet - A.waist), 0, 1));
    };
    var hairW = function (y) {
      if (y > A.hairTip + 40) return 0;
      return 0.25 + 0.75 * smoothstep(A.headTop + 70, A.hairTip, y);
    };
    var skirtW = function (y) {
      return bump(A.skirtTop - 50, A.skirtTop + 30, A.skirtBottom + 120, y);
    };

    this.offFn = function (y) {
      var hw = headW(y);
      return bend(y) * AMP.sway * (swayV + r * 0.9)
        + gx * AMP.gazeX * hw
        + AMP.lean * gx * (A.neck - y) * hw          // 头部侧倾
        + hairW(y) * AMP.hair * (hairV + r * 1.6)
        + skirtW(y) * AMP.skirt * skirtV;
    };

    this.computeMap();
    this.draw();
  };

  Kabibi.prototype.wake = function () {
    if (this.running || this.stopped) return;
    if (this.static && this.drewStatic) return;   // 静帧已画好，别再空转
    this.running = true;
    this.last = 0;
    this.loop();
  };

  Kabibi.prototype.bind = function () {
    var self = this;
    window.addEventListener('pointermove', function (e) {
      var box = self.canvas.getBoundingClientRect();
      var cx = box.left + box.width / 2, cy = box.top + box.height * 0.42;
      self.tgx = clamp((e.clientX - cx) / (window.innerWidth * 0.42), -1, 1);
      self.tgy = clamp((e.clientY - cy) / (window.innerHeight * 0.5), -1, 1);
      self.pointerAt = self.t;
      self.wake();
    }, { passive: true });

    self.canvas.addEventListener('pointerdown', function () {
      self.reactT = 0;
      self.blinks.push([self.t, 0.12]);
      self.blinks.push([self.t + 0.22, 0.14]);
      if (self.static) { self.static = false; self.blinkAt = self.t + 2.5; }
      self.wake();
    });

    window.addEventListener('resize', function () { self.resize(); self.wake(); },
      { passive: true });

    document.addEventListener('visibilitychange', function () {
      self.visible = !document.hidden && self.inView !== false;
      if (self.visible) self.wake();
    });

    if (window.matchMedia) {                    // 系统里改了「减少动效」也能跟上
      var mq = window.matchMedia('(prefers-reduced-motion: reduce)');
      var onChange = function () {
        self.static = mq.matches;
        if (!self.static) { self.blinkAt = self.t + 1.0; self.drewStatic = false; self.wake(); }
      };
      if (mq.addEventListener) mq.addEventListener('change', onChange);
      else if (mq.addListener) mq.addListener(onChange);
    }

    if (window.IntersectionObserver) {
      new IntersectionObserver(function (entries) {
        self.inView = entries[0].isIntersecting;
        self.visible = self.inView && !document.hidden;
        if (self.visible) self.wake();
      }, { threshold: 0 }).observe(this.canvas);
    }
  };

  function init() {
    var roots = document.querySelectorAll('[data-kabibi]');
    Array.prototype.forEach.call(roots, function (root) {
      var inst = new Kabibi(root);
      var canvas = document.createElement('canvas');
      canvas.setAttribute('role', 'img');
      canvas.setAttribute('aria-label', '卡比比酱（会动的看板娘）');
      canvas.title = '点她一下';
      inst.canvas = canvas;
      canvas.kabibi = inst;   // 调试入口：控制台里看状态
      inst.ctx = canvas.getContext('2d');
      inst.load().then(function () {
        root.textContent = '';
        root.appendChild(canvas);
        inst.resize();
        inst.bind();
        inst.wake();
      }).catch(function (err) {
        // 素材加载失败就保留原来的颜文字
        if (window.console) console.warn('[kabibi] ' + err.message);
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
