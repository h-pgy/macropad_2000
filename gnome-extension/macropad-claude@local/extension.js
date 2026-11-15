// Macropad 2000 — extensão do GNOME Shell (45+).
//
// Conversa com o daemon pelo socket Unix dele:
//   1. avisa qual janela está em foco: {"cmd":"focus","app":..,"wm_class":..,"title":..}
//   2. abre/foca aplicativos quando o daemon pede: {"type":"launch","app":"x.desktop","new_window":false}
//      e abre sites no navegador padrão: {"type":"open_url","url":"https://..."}
//   3. mostra o modo (perfil fixo / automático):
//      {"type":"mode","locked":true,"locked_profile":"Global","profile":"Global","notify":true}
//      - aviso rápido no centro da tela, com a cor do modo, quando muda;
//      - badge colorido no canto superior esquerdo, sempre visível (menu: alternar).
//   4. captura um atalho para a UI: {"type":"capture"} -> {"cmd":"captured",...}
//      O Shell pega o teclado (grab modal), então atalhos do sistema como Ctrl+Alt+T
//      chegam aqui em vez de serem executados (o navegador nunca os veria).
// Toda a lógica (perfis, regras) fica no daemon, que reinicia sem logout.

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

// automático = tecladinho; fixo = tecladinho + cadeadinho
const ICON_KEYBOARD = 'input-keyboard-symbolic';
const ICON_LOCK = 'changes-prevent-symbolic';
// uma cor por modo: badge da barra e aviso (no amarelo, texto escuro para dar contraste)
const COLOR_LOCKED = '#f5c211'; // amarelo
const COLOR_AUTO = '#1c71d8';   // azul
const INK_LOCKED = '#241f31';
const INK_AUTO = '#ffffff';
const TOAST_MS = 1300;

const RETRY_SECONDS = 3;
const TITLE_THROTTLE_MS = 300; // o título do Claude muda várias vezes por segundo (spinner)
const MAX_QUEUE = 100; // daemon parou de ler: derruba a conexão em vez de acumular memória

const CAPTURE_SECONDS = 10;
const MODIFIER_KEYVALS = new Set([
    Clutter.KEY_Shift_L, Clutter.KEY_Shift_R, Clutter.KEY_Control_L, Clutter.KEY_Control_R,
    Clutter.KEY_Alt_L, Clutter.KEY_Alt_R, Clutter.KEY_Super_L, Clutter.KEY_Super_R,
    Clutter.KEY_Meta_L, Clutter.KEY_Meta_R, Clutter.KEY_Hyper_L, Clutter.KEY_Hyper_R,
    Clutter.KEY_ISO_Level3_Shift, Clutter.KEY_Caps_Lock, Clutter.KEY_Num_Lock,
]);

const encoder = new TextEncoder();

export default class MacropadClaudeExtension extends Extension {
    enable() {
        this._enabled = true;
        this._conn = null;
        this._out = null;
        this._cancel = null;
        this._queue = [];
        this._writing = false;
        this._retryId = 0;
        this._throttleId = 0;
        this._win = null;
        this._titleId = 0;
        this._indicator = null;
        this._badgeLabel = null;
        this._badgeIcon = null;
        this._badgeLock = null;
        this._menuItem = null;
        this._locked = false;
        this._toast = null;
        this._toastId = 0;
        this._capture = null;
        this._sockPath = GLib.build_filenamev([GLib.get_user_runtime_dir(), 'macropad-claude.sock']);
        this._focusId = global.display.connect('notify::focus-window', () => this._onFocus());
        this._connect();
    }

    disable() {
        this._enabled = false;
        global.display.disconnect(this._focusId);
        this._unwatchTitle();
        if (this._retryId)
            GLib.source_remove(this._retryId);
        if (this._throttleId)
            GLib.source_remove(this._throttleId);
        this._retryId = this._throttleId = 0;
        this._endCapture();
        this._close();
        this._setIndicator(null);
        this._hideToast();
    }

    // ---------------------------------------------------------------- conexão

    _connect() {
        this._retryId = 0;
        const cancel = new Gio.Cancellable();
        this._cancel = cancel;
        const client = new Gio.SocketClient();
        const addr = new Gio.UnixSocketAddress({path: this._sockPath});
        client.connect_async(addr, cancel, (c, res) => {
            if (!this._enabled || cancel !== this._cancel)
                return;
            try {
                this._conn = c.connect_finish(res);
            } catch (e) {
                this._retry();
                return;
            }
            this._out = this._conn.get_output_stream();
            const input = new Gio.DataInputStream({base_stream: this._conn.get_input_stream()});
            this._send({cmd: 'hello', role: 'extension', capture: true, open_url: true});
            this._onFocus();
            this._readLoop(this._conn, input);
        });
    }

    _retry() {
        this._close();
        this._setIndicator(null); // daemon fora: não mostrar um estado que pode estar errado
        if (this._enabled && !this._retryId) {
            this._retryId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, RETRY_SECONDS, () => {
                this._connect();
                return GLib.SOURCE_REMOVE;
            });
        }
    }

    _close() {
        this._endCapture(); // sem daemon não há para quem mandar o atalho
        if (this._cancel) {
            this._cancel.cancel(); // aborta connect/leitura/escrita pendentes desta conexão
            this._cancel = null;
        }
        this._queue = [];
        this._writing = false;
        if (this._conn) {
            try {
                this._conn.close(null);
            } catch (e) {}
        }
        this._conn = null;
        this._out = null;
    }

    _readLoop(conn, input) {
        input.read_line_async(GLib.PRIORITY_DEFAULT, this._cancel, (s, res) => {
            if (!this._enabled || conn !== this._conn)
                return; // conexão antiga (já fechada/substituída)
            let line;
            try {
                [line] = s.read_line_finish_utf8(res);
            } catch (e) {
                line = null;
            }
            if (line === null) { // daemon fechou
                this._retry();
                return;
            }
            try {
                const msg = JSON.parse(line);
                if (msg.type === 'launch')
                    this._launch(msg);
                else if (msg.type === 'open_url')
                    this._openUrl(msg);
                else if (msg.type === 'mode')
                    this._onMode(msg);
                else if (msg.type === 'capture')
                    this._startCapture();
                else if (msg.type === 'capture_cancel')
                    this._endCapture();
            } catch (e) {
                console.warn(`macropad-claude: ${e}`);
            }
            this._readLoop(conn, input);
        });
    }

    // Escrita assíncrona por fila: nunca bloqueia a thread principal do Shell,
    // mesmo que o daemon trave sem fechar o socket e o buffer encha.
    _send(obj) {
        if (!this._out)
            return;
        if (obj.cmd === 'focus') // só o foco mais recente importa
            this._queue = this._queue.filter(m => m.cmd !== 'focus');
        this._queue.push({cmd: obj.cmd, data: encoder.encode(`${JSON.stringify(obj)}\n`)});
        if (this._queue.length > MAX_QUEUE) {
            console.warn('macropad-claude: daemon não está lendo o socket; reconectando');
            this._retry();
            return;
        }
        this._flush();
    }

    _flush() {
        if (this._writing || !this._out || !this._queue.length)
            return;
        this._writing = true;
        this._write(this._out, this._queue.shift().data);
    }

    _write(out, data) {
        out.write_bytes_async(new GLib.Bytes(data), GLib.PRIORITY_DEFAULT, this._cancel, (s, res) => {
            if (!this._enabled || out !== this._out)
                return; // conexão antiga: _close() já zerou fila e estado
            let n;
            try {
                n = s.write_bytes_finish(res);
            } catch (e) {
                this._retry();
                return;
            }
            if (n < data.length) { // escrita parcial: manda o resto
                this._write(out, data.subarray(n));
                return;
            }
            this._writing = false;
            this._flush();
        });
    }

    // ---------------------------------------------------------------- foco

    _onFocus() {
        this._unwatchTitle();
        const w = global.display.focus_window;
        if (w) {
            this._win = w;
            this._titleId = w.connect('notify::title', () => this._throttledSend());
        }
        this._sendFocus();
    }

    _unwatchTitle() {
        if (this._win && this._titleId) {
            try {
                this._win.disconnect(this._titleId);
            } catch (e) {}
        }
        this._win = null;
        this._titleId = 0;
    }

    _throttledSend() {
        if (this._throttleId)
            return;
        this._throttleId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, TITLE_THROTTLE_MS, () => {
            this._throttleId = 0;
            this._sendFocus();
            return GLib.SOURCE_REMOVE;
        });
    }

    _sendFocus() {
        const w = global.display.focus_window;
        if (!w) {
            this._send({cmd: 'focus', app: null, app_name: null, wm_class: null, title: null});
            return;
        }
        const app = Shell.WindowTracker.get_default().get_window_app(w);
        this._send({
            cmd: 'focus',
            app: app ? app.get_id() : null,
            app_name: app ? app.get_name() : null,
            wm_class: w.get_wm_class(),
            title: w.get_title(),
        });
    }

    // ---------------------------------------------------------------- abrir apps

    _launch(msg) {
        const app = Shell.AppSystem.get_default().lookup_app(msg.app);
        if (!app)
            return;
        if (msg.new_window && app.can_open_new_window())
            app.open_new_window(-1);
        else
            app.activate(); // foca se já estiver aberto, senão abre (como clicar na dock)
    }

    _openUrl(msg) {
        if (!/^https?:\/\//i.test(msg.url ?? ''))
            return;
        // o contexto do Shell dá ao navegador o direito de vir para a frente (sem "está pronto")
        const ctx = global.create_app_launch_context(0, -1);
        try {
            Gio.AppInfo.launch_default_for_uri(msg.url, ctx);
        } catch (e) {
            console.error(`macropad-claude: não abriu ${msg.url}: ${e.message}`);
        }
    }

    // ---------------------------------------------------------------- modo Global fixo

    _onMode(msg) {
        const locked = Boolean(msg.locked);
        const fixed = msg.locked_profile ?? msg.profile ?? 'Global';
        this._setIndicator(locked, fixed);
        if (!msg.notify)
            return;
        this._showToast(locked, locked ? `Macropad: ${fixed} fixo` : `Macropad: automático · ${msg.profile ?? '?'}`);
    }

    _colors(locked) {
        return `background-color: ${locked ? COLOR_LOCKED : COLOR_AUTO}; color: ${locked ? INK_LOCKED : INK_AUTO};`;
    }

    _badgeStyle(locked) {
        return `${this._colors(locked)} border-radius: 999px; padding: 2px 10px; font-weight: bold; spacing: 5px;`;
    }

    // tecladinho, com o cadeadinho do lado quando fixo
    _modeIcons(locked, size) {
        const color = `color: ${locked ? INK_LOCKED : INK_AUTO};`;
        const box = new St.BoxLayout({style: 'spacing: 1px;', y_align: Clutter.ActorAlign.CENTER});
        const keyboard = new St.Icon({icon_name: ICON_KEYBOARD, icon_size: size, style: color,
            y_align: Clutter.ActorAlign.CENTER});
        const lock = new St.Icon({icon_name: ICON_LOCK, icon_size: Math.round(size * 0.7), style: color,
            y_align: Clutter.ActorAlign.CENTER, visible: locked});
        box.add_child(keyboard);
        box.add_child(lock);
        return [box, keyboard, lock];
    }

    // locked: true = Global fixo, false = automático, null = daemon fora (esconde)
    _setIndicator(locked, fixed = 'Global') {
        if (locked === null) {
            this._indicator?.destroy();
            this._indicator = this._badgeLabel = this._badgeIcon = this._badgeLock = this._badge = this._menuItem = null;
            return;
        }
        this._locked = locked;
        if (!this._indicator) {
            const button = new PanelMenu.Button(0.5, 'Macropad 2000', false);
            this._badge = new St.BoxLayout({y_align: Clutter.ActorAlign.CENTER});
            const [icons, keyboard, lock] = this._modeIcons(locked, 16);
            this._badgeIcon = keyboard;
            this._badgeLock = lock;
            this._badgeLabel = new St.Label({y_align: Clutter.ActorAlign.CENTER});
            this._badge.add_child(icons);
            this._badge.add_child(this._badgeLabel);
            button.add_child(this._badge);
            this._menuItem = new PopupMenu.PopupMenuItem('');
            this._menuItem.connect('activate', () => this._send({cmd: 'lock', on: !this._locked}));
            button.menu.addMenuItem(this._menuItem);
            Main.panel.addToStatusArea(this.uuid, button, 1, 'left'); // canto superior esquerdo
            this._indicator = button;
        }
        this._badge.style = this._badgeStyle(locked);
        const ink = `color: ${locked ? INK_LOCKED : INK_AUTO};`;
        this._badgeIcon.style = this._badgeLock.style = ink;
        this._badgeLock.visible = locked;
        this._badgeLabel.text = locked ? fixed.toUpperCase() : 'AUTO';
        this._menuItem.label.text = locked
            ? 'Voltar ao automático (perfil pela janela)'
            : 'Fixar no Global';
    }

    _showToast(locked, text) {
        this._hideToast();
        const box = new St.BoxLayout({
            style: `${this._colors(locked)} ` +
                'border-radius: 20px; padding: 18px 30px; spacing: 14px; ' +
                'box-shadow: 0 4px 16px rgba(0,0,0,0.4);',
            opacity: 0,
        });
        box.add_child(this._modeIcons(locked, 36)[0]);
        box.add_child(new St.Label({text, style: 'font-size: 22px; font-weight: bold;',
            y_align: Clutter.ActorAlign.CENTER}));
        Main.layoutManager.uiGroup.add_child(box);
        const mon = Main.layoutManager.primaryMonitor;
        const [, w] = box.get_preferred_width(-1);
        const [, hgt] = box.get_preferred_height(w);
        box.set_position(mon.x + Math.floor((mon.width - w) / 2), mon.y + Math.floor(mon.height * 0.72 - hgt / 2));
        this._toast = box;
        box.ease({opacity: 255, duration: 120, mode: Clutter.AnimationMode.EASE_OUT_QUAD});
        this._toastId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, TOAST_MS, () => {
            this._toastId = 0;
            box.ease({
                opacity: 0, duration: 300, mode: Clutter.AnimationMode.EASE_OUT_QUAD,
                onComplete: () => {
                    if (this._toast === box)
                        this._hideToast();
                },
            });
            return GLib.SOURCE_REMOVE;
        });
    }

    _hideToast() {
        if (this._toastId)
            GLib.source_remove(this._toastId);
        this._toastId = 0;
        this._toast?.destroy();
        this._toast = null;
    }

    // ---------------------------------------------------------------- captura de atalho

    _startCapture() {
        if (this._capture) // já capturando (2º pedido): o resultado vai para quem pediu por último
            return;
        const label = new St.Label({
            text: 'Macropad: aperte o atalho…\n(Esc ou clique cancela —\npara mapear o Esc, use o chip «esc»)',
            style: 'font-size: 1.4em; text-align: center; padding: 24px 36px; border-radius: 16px;'
                + 'background-color: rgba(30,30,30,0.92); color: white;',
        });
        const actor = new St.Bin({reactive: true, can_focus: true, child: label});
        Main.layoutManager.addTopChrome(actor);
        const mon = Main.layoutManager.primaryMonitor;
        const [, natW] = actor.get_preferred_width(-1);
        const [, natH] = actor.get_preferred_height(natW);
        actor.set_position(mon.x + Math.floor((mon.width - natW) / 2), mon.y + Math.floor((mon.height - natH) / 2));

        // Grab modal sem nenhum ActionMode: o Shell não dispara atalhos (os seus, os do
        // mutter e os personalizados do gsd, como Ctrl+Alt+T) enquanto ele durar.
        const grab = Main.pushModal(actor, {actionMode: Shell.ActionMode.NONE});
        if ((grab.get_seat_state() & Clutter.GrabState.KEYBOARD) === 0) {
            Main.popModal(grab);
            actor.destroy();
            this._send({cmd: 'captured', ok: false, error: 'grab'});
            return;
        }
        const cap = {actor, grab, timeoutId: 0};
        this._capture = cap;
        actor.grab_key_focus();
        actor.connect('key-press-event', (a, event) => {
            this._onCaptureKey(event);
            return Clutter.EVENT_STOP;
        });
        actor.connect('button-press-event', () => {
            this._finishCapture({ok: false, cancelled: true});
            return Clutter.EVENT_STOP;
        });
        cap.timeoutId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, CAPTURE_SECONDS, () => {
            cap.timeoutId = 0;
            this._finishCapture({ok: false, timeout: true});
            return GLib.SOURCE_REMOVE;
        });
    }

    _onCaptureKey(event) {
        const keyval = event.get_key_symbol();
        if (MODIFIER_KEYVALS.has(keyval))
            return; // espera a tecla "de verdade"
        const state = event.get_state();
        const mods = [];
        if (state & Clutter.ModifierType.CONTROL_MASK)
            mods.push('ctrl');
        if (state & Clutter.ModifierType.MOD1_MASK)
            mods.push('alt');
        if (state & Clutter.ModifierType.SHIFT_MASK)
            mods.push('shift');
        if (state & (Clutter.ModifierType.SUPER_MASK | Clutter.ModifierType.MOD4_MASK))
            mods.push('super');
        if (keyval === Clutter.KEY_Escape && !mods.length) {
            this._finishCapture({ok: false, cancelled: true});
            return;
        }
        // keycode de hardware (X11 = evdev + 8): o daemon traduz com a mesma tabela que usa para teclar
        this._finishCapture({ok: true, code: event.get_key_code() - 8, mods});
    }

    _finishCapture(result) {
        if (!this._capture)
            return;
        this._endCapture();
        this._send({cmd: 'captured', ...result});
    }

    _endCapture() {
        const cap = this._capture;
        if (!cap)
            return;
        this._capture = null;
        if (cap.timeoutId)
            GLib.source_remove(cap.timeoutId);
        Main.popModal(cap.grab);
        cap.actor.destroy();
    }
}
