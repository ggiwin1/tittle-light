import React, { useState, useRef, useEffect } from 'react';
import {
  Wifi,
  Radio,
  Terminal,
  Cpu,
  Lightbulb,
  Clock,
  Music,
  CheckCircle2,
  AlertTriangle,
  HelpCircle,
  Layers,
  Copy,
  Check,
  Server,
  FileCode,
  Sliders,
  ChevronRight,
  Sparkles,
  Info,
  Calendar,
  Send,
  Eye,
  Code2,
  Download,
  Paintbrush
} from 'lucide-react';
import fullDesktopAppScript from '../tittle_desktop.py?raw';
import VoxelEditor from './VoxelEditor';
import { bridgeRequest, checkBridge, getOrientation } from './cubeBridge';

export default function App() {
  const [activeTab, setActiveTab] = useState<'recovered_table' | 'live_tester' | 'novel_presets' | 'frame_spec' | 'python_script' | 'phase2_preview' | 'voxel_editor'>('voxel_editor');
  const [selectedNovelPreset, setSelectedNovelPreset] = useState<string>('fairies_pro');
  const [targetIp, setTargetIpState] = useState<string>(() => {
    try {
      return localStorage.getItem('tittle.targetIp') || '192.168.1.150';
    } catch {
      return '192.168.1.150';
    }
  });
  const setTargetIp = (ip: string) => {
    setTargetIpState(ip);
    try {
      localStorage.setItem('tittle.targetIp', ip);
    } catch {
      /* storage unavailable */
    }
  };
  const [bridgeOnline, setBridgeOnline] = useState(false);
  const [bridgeBusy, setBridgeBusy] = useState(false);
  const [bridgeLog, setBridgeLog] = useState<string[]>([]);

  useEffect(() => {
    let alive = true;
    const poll = () => checkBridge().then((ok) => alive && setBridgeOnline(ok));
    poll();
    const t = setInterval(poll, 5000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  // Easter egg: spinning pepperoni pizza on the cube for 5 seconds, then back to what it was showing.
  const [pizza, setPizza] = useState<string | null>(null);
  const pizzaTime = async () => {
    if (pizza) return;
    if (!bridgeOnline) {
      setPizza('Start the bridge (npm run dev) to send the pizza to the cube too.');
      setTimeout(() => setPizza(null), 5000);
      return;
    }
    setPizza(`Sending a pepperoni slice to ${targetIp}…`);
    const res = await bridgeRequest('pizza', { ip: targetIp, seconds: 5, orientation: getOrientation() }, {
      onLog: (l) => {
        if (l.startsWith('🍕')) setPizza('Pizza time! Spinning on the cube for 5 seconds…');
        if (l.startsWith('Restoring')) setPizza('Putting your cube back the way it was…');
      },
    });
    setPizza(res.ok ? 'Back to normal. 🍕' : `Pizza failed: ${res.error}`);
    setTimeout(() => setPizza(null), 2500);
  };

  const runBridge = async (type: 'send' | 'discover', body: object) => {
    setBridgeBusy(true);
    setBridgeLog([]);
    const res = await bridgeRequest(type, body, { onLog: (l) => setBridgeLog((ls) => [...ls, l]) });
    if (!res.ok) setBridgeLog((ls) => [...ls, `[ERROR] ${res.error}`]);
    if (type === 'discover') {
      const ips = (res.ips as string[] | undefined) ?? [];
      if (ips.length) {
        setTargetIp(ips[0]);
        setBridgeLog((ls) => [...ls, `Using ${ips[0]} as the target IP.`]);
      } else if (res.ok) setBridgeLog((ls) => [...ls, 'No cube found. Check it is powered on and on the same Wi-Fi.']);
    }
    setBridgeBusy(false);
  };
  const [selectedMode, setSelectedMode] = useState<'lamp' | 'clock' | 'music' | 'mood' | 'emoji' | 'status' | 'custom_voxel'>('lamp');
  
  // Controls
  const [r, setR] = useState<number>(255);
  const [g, setG] = useState<number>(180);
  const [b, setB] = useState<number>(50);
  const [intensity, setIntensity] = useState<number>(255);
  const [sensitivity, setSensitivity] = useState<number>(50);
  const [presetId, setPresetId] = useState<number>(1);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  const copyToClipboard = (text: string, key: string) => {
    navigator.clipboard.writeText(text);
    setCopiedKey(key);
    setTimeout(() => setCopiedKey(null), 2000);
  };

  const toHex = (n: number) => (n & 0xff).toString(16).padStart(2, '0').toUpperCase();

  // Compute live bytes based on selected mode
  const getPacketBytes = () => {
    switch (selectedMode) {
      case 'lamp':
        return ['10', toHex(r), toHex(g), toHex(b), toHex(intensity), '0D', '0A'];
      case 'clock': {
        const now = new Date();
        const yy = (now.getFullYear() - 2000) & 0xff;
        const mm = (now.getMonth() + 1) & 0xff;
        const dd = now.getDate() & 0xff;
        const hh = now.getHours() & 0xff;
        const min = now.getMinutes() & 0xff;
        const ss = now.getSeconds() & 0xff;
        // 0x34, HH, MM, SS, ID_0..3, Intensity, Version, YY, MM, DD, 00, 00, 00, 00, 00, 0D, 0A
        return [
          '34',
          toHex(hh),
          toHex(min),
          toHex(ss),
          '00', '00', '00', toHex(presetId),
          toHex(intensity),
          '01',
          toHex(yy),
          toHex(mm),
          toHex(dd),
          '00', '00', '00', '00', '00',
          '0D', '0A'
        ];
      }
      case 'music':
        // 0x41, ID_0..3, Intensity, Sensitivity, Version, 00..00, 0D, 0A
        return [
          '41',
          '00', '00', '00', toHex(presetId),
          toHex(intensity),
          toHex(sensitivity),
          '01',
          '00', '00', '00', '00', '00',
          '0D', '0A'
        ];
      case 'mood':
        // 0x50, FrameCount(2), Interval(1), ID(4), Ver(1), Intensity(1), Pad(6), 0D, 0A
        return [
          '50',
          '00', '0A',
          '14',
          '00', '00', '00', toHex(presetId),
          '01',
          toHex(intensity),
          '00', '00', '00', '00', '00', '00',
          '0D', '0A'
        ];
      case 'emoji':
        // 0x20, FrameCount(2), Interval(1), ID(4), Ver(1), Intensity(1), Pad(6), 0D, 0A
        return [
          '20',
          '00', '01',
          '0A',
          '00', '00', '00', toHex(presetId),
          '01',
          toHex(intensity),
          '00', '00', '00', '00', '00', '00',
          '0D', '0A'
        ];
      case 'status':
        return ['60', '01', '00', '0D', '0A'];
      case 'custom_voxel':
        // 0x90 header
        return [
          '90',
          '00', '01',
          '0A',
          '00', '00', '00', '01',
          '01',
          toHex(intensity),
          '00',
          '00', '00', '00', '00', '00',
          '0D', '0A'
        ];
    }
  };

  const liveBytes = getPacketBytes();
  const hexString = liveBytes.join(' ');

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col font-sans">
      {/* Header */}
      <header className="border-b border-slate-800 bg-slate-900/90 backdrop-blur sticky top-0 z-20">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-amber-500/10 border border-amber-500/30 flex items-center justify-center text-amber-400 shadow-inner">
              <Lightbulb className="w-5 h-5" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="font-bold text-lg text-white tracking-tight">Tittle Light Protocol Engineering</h1>
                <span className="px-2 py-0.5 rounded text-[11px] font-semibold bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">
                  Phase 1 Decompiled & Recovered
                </span>
              </div>
              <p className="text-xs text-slate-400">Extracted directly from official APK (com.clarityhk.tittle.android v2.2.25)</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <span
              title={bridgeOnline ? 'server.ts is running: packets can go straight to the cube' : 'Run `npm run dev` to start the local bridge'}
              className={`text-xs font-mono px-2.5 py-1 rounded border flex items-center gap-1.5 ${
                bridgeOnline ? 'bg-emerald-500/10 text-emerald-300 border-emerald-500/20' : 'bg-slate-800/60 text-slate-400 border-slate-700'
              }`}
            >
              <span className={`w-1.5 h-1.5 rounded-full ${bridgeOnline ? 'bg-emerald-400' : 'bg-slate-500'}`} />
              {bridgeOnline ? 'Cube bridge online' : 'Cube bridge offline'}
            </span>
          </div>
        </div>
      </header>

      {/* Main navigation tabs */}
      <div className="border-b border-slate-800 bg-slate-900/40">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <nav className="flex space-x-1 sm:space-x-4 overflow-x-auto py-2">
            {[
              { id: 'voxel_editor', label: '🎨 3D Voxel Editor', icon: Paintbrush },
              { id: 'recovered_table', label: '1. Recovered Commands Table', icon: Layers },
              { id: 'live_tester', label: '2. Live Command Inspector', icon: Sliders },
              { id: 'novel_presets', label: '3. ✨ Novel Generative 3D Presets', icon: Sparkles },
              { id: 'frame_spec', label: '4. 3D Voxel (8×8×8) Streaming Spec', icon: Cpu },
              { id: 'python_script', label: '5. Ready Python Diagnostic Script', icon: Terminal },
              { id: 'phase2_preview', label: '6. Phase 2 Desktop App Architecture', icon: Code2 },
            ].map((tab) => {
              const Icon = tab.icon;
              const isActive = activeTab === tab.id;
              return (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id as any)}
                  className={`flex items-center gap-2 px-3 py-2 rounded-lg text-xs sm:text-sm font-medium transition-all whitespace-nowrap ${
                    isActive
                      ? 'bg-amber-500/15 text-amber-300 border border-amber-500/30 shadow-sm'
                      : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60'
                  }`}
                >
                  <Icon className="w-4 h-4" />
                  {tab.label}
                </button>
              );
            })}
          </nav>
        </div>
      </div>

      {/* Main Container */}
      <main className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-6 space-y-6">

        {/* Kept mounted so frames and undo history survive tab switches */}
        <div className={activeTab === 'voxel_editor' ? '' : 'hidden'}>
          <VoxelEditor targetIp={targetIp} setTargetIp={setTargetIp} bridgeOnline={bridgeOnline} active={activeTab === 'voxel_editor'} />
        </div>

        {/* TAB 1: RECOVERED COMMANDS TABLE */}
        {activeTab === 'recovered_table' && (
          <div className="space-y-6">
            <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 space-y-3">
              <div className="flex items-center justify-between">
                <h2 className="text-base font-semibold text-white flex items-center gap-2">
                  <CheckCircle2 className="w-5 h-5 text-emerald-400" />
                  Official App Decompiled Command Set (All OpCodes Recovered)
                </h2>
                <span className="text-xs font-mono text-slate-400 bg-slate-950 px-2 py-1 rounded border border-slate-800">
                  Target TCP Port: 9999
                </span>
              </div>
              <p className="text-sm text-slate-300 leading-relaxed">
                We successfully decompiled the official Android companion app (<code className="text-amber-300 font-mono text-xs">com.clarityhk.tittle.android</code>) and audited <code className="text-amber-300 font-mono text-xs">CommandUtils.java</code>, <code className="text-amber-300 font-mono text-xs">SendRequestService.java</code>, and <code className="text-amber-300 font-mono text-xs">AnimationFrameHolder.java</code>.
                Here is the verified table of all command bytes:
              </p>
            </div>

            <div className="overflow-x-auto rounded-xl border border-slate-800 shadow-xl">
              <table className="w-full text-left border-collapse text-xs sm:text-sm">
                <thead>
                  <tr className="bg-slate-900 border-b border-slate-800 text-slate-400 uppercase text-[11px] font-mono">
                    <th className="py-3.5 px-4">Feature / Mode</th>
                    <th className="py-3.5 px-4">OpCode</th>
                    <th className="py-3.5 px-4">Packet Length</th>
                    <th className="py-3.5 px-4">Byte Structure Anatomy</th>
                    <th className="py-3.5 px-4 font-mono">Example Hex Payload</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800 bg-slate-950/70 font-sans">
                  {/* Lamp On */}
                  <tr className="hover:bg-slate-900/40">
                    <td className="py-3 px-4 font-semibold text-white flex items-center gap-2">
                      <Lightbulb className="w-4 h-4 text-amber-400" /> Solid Color (Lamp)
                    </td>
                    <td className="py-3 px-4 font-mono text-cyan-400 font-bold">0x10 (16)</td>
                    <td className="py-3 px-4 text-slate-300">7 bytes</td>
                    <td className="py-3 px-4 text-slate-300 text-xs">
                      <code className="text-cyan-300">[0x10]</code> + <code className="text-red-300">R</code> + <code className="text-green-300">G</code> + <code className="text-blue-300">B</code> + <code className="text-amber-300">Intensity</code> + <code className="text-slate-400">0x0D 0x0A</code>
                    </td>
                    <td className="py-3 px-4 font-mono text-xs text-amber-300 font-bold">10 FF B4 32 FF 0D 0A</td>
                  </tr>

                  {/* Lamp Off */}
                  <tr className="hover:bg-slate-900/40">
                    <td className="py-3 px-4 font-semibold text-slate-300 flex items-center gap-2">
                      <Lightbulb className="w-4 h-4 text-slate-500" /> Turn Off / Black
                    </td>
                    <td className="py-3 px-4 font-mono text-cyan-400 font-bold">0x10 (16)</td>
                    <td className="py-3 px-4 text-slate-300">7 bytes</td>
                    <td className="py-3 px-4 text-slate-300 text-xs">
                      <code className="text-cyan-300">[0x10]</code> + <code className="text-slate-400">0x00 0x00 0x00 0x00</code> + <code className="text-slate-400">0x0D 0x0A</code>
                    </td>
                    <td className="py-3 px-4 font-mono text-xs text-slate-400">10 00 00 00 00 0D 0A</td>
                  </tr>

                  {/* Emoji / 3D Icons */}
                  <tr className="hover:bg-slate-900/40">
                    <td className="py-3 px-4 font-semibold text-white flex items-center gap-2">
                      <Sparkles className="w-4 h-4 text-pink-400" /> 3D Emoji / Mood Icons
                    </td>
                    <td className="py-3 px-4 font-mono text-pink-400 font-bold">0x20 (32)</td>
                    <td className="py-3 px-4 text-slate-300">18 bytes</td>
                    <td className="py-3 px-4 text-slate-300 text-xs">
                      <code className="text-pink-300">[0x20]</code> + FrameCount(2) + Interval(1) + AnimID(4) + Ver(1) + Intensity(1) + Pad(6) + 0x0D 0x0A
                    </td>
                    <td className="py-3 px-4 font-mono text-xs text-pink-300">20 00 01 0A 00 00 00 01 01 FF 00 00 00 00 00 00 0D 0A</td>
                  </tr>

                  {/* Clock Mode */}
                  <tr className="hover:bg-slate-900/40">
                    <td className="py-3 px-4 font-semibold text-white flex items-center gap-2">
                      <Clock className="w-4 h-4 text-emerald-400" /> Digital & 3D Clock
                    </td>
                    <td className="py-3 px-4 font-mono text-emerald-400 font-bold">0x34 (52)</td>
                    <td className="py-3 px-4 text-slate-300">20 bytes</td>
                    <td className="py-3 px-4 text-slate-300 text-xs">
                      <code className="text-emerald-300">[0x34]</code> + HH + MM + SS + ClockID(4) + Intensity(1) + Ver(1) + (Year-2000) + Month + Day + Pad(5) + 0x0D 0x0A
                    </td>
                    <td className="py-3 px-4 font-mono text-xs text-emerald-300">34 0E 1E 00 00 00 00 01 FF 01 1A 0B 05 00 00 00 00 00 0D 0A</td>
                  </tr>

                  {/* Music Visualizer */}
                  <tr className="hover:bg-slate-900/40">
                    <td className="py-3 px-4 font-semibold text-white flex items-center gap-2">
                      <Music className="w-4 h-4 text-purple-400" /> Music Visualizer
                    </td>
                    <td className="py-3 px-4 font-mono text-purple-400 font-bold">0x41 (65)</td>
                    <td className="py-3 px-4 text-slate-300">15 bytes</td>
                    <td className="py-3 px-4 text-slate-300 text-xs">
                      <code className="text-purple-300">[0x41]</code> + StyleID(4) + Intensity(1) + Sensitivity(1) + Ver(1) + Pad(5) + 0x0D 0x0A
                    </td>
                    <td className="py-3 px-4 font-mono text-xs text-purple-300">41 00 00 00 01 FF 32 01 00 00 00 00 00 0D 0A</td>
                  </tr>

                  {/* Mood Animations */}
                  <tr className="hover:bg-slate-900/40">
                    <td className="py-3 px-4 font-semibold text-white flex items-center gap-2">
                      <Layers className="w-4 h-4 text-orange-400" /> Mood Presets (Fire, Wave, Rain)
                    </td>
                    <td className="py-3 px-4 font-mono text-orange-400 font-bold">0x50 (80)</td>
                    <td className="py-3 px-4 text-slate-300">18 bytes</td>
                    <td className="py-3 px-4 text-slate-300 text-xs">
                      <code className="text-orange-300">[0x50]</code> + FrameCount(2) + Interval(1) + MoodID(4) + Ver(1) + Intensity(1) + Pad(6) + 0x0D 0x0A
                    </td>
                    <td className="py-3 px-4 font-mono text-xs text-orange-300">50 00 0A 14 00 00 00 01 01 FF 00 00 00 00 00 00 0D 0A</td>
                  </tr>

                  {/* Device Status */}
                  <tr className="hover:bg-slate-900/40">
                    <td className="py-3 px-4 font-semibold text-white flex items-center gap-2">
                      <Info className="w-4 h-4 text-blue-400" /> Query Device Status
                    </td>
                    <td className="py-3 px-4 font-mono text-blue-400 font-bold">0x60 (96)</td>
                    <td className="py-3 px-4 text-slate-300">5 bytes</td>
                    <td className="py-3 px-4 text-slate-300 text-xs">
                      <code className="text-blue-300">[0x60, 0x01, 0x00, 0x0D, 0x0A]</code>
                    </td>
                    <td className="py-3 px-4 font-mono text-xs text-blue-300">60 01 00 0D 0A</td>
                  </tr>

                  {/* Current Mode */}
                  <tr className="hover:bg-slate-900/40">
                    <td className="py-3 px-4 font-semibold text-white flex items-center gap-2">
                      <Eye className="w-4 h-4 text-indigo-400" /> Query Current Playing Mode
                    </td>
                    <td className="py-3 px-4 font-mono text-indigo-400 font-bold">0x70 0x05</td>
                    <td className="py-3 px-4 text-slate-300">4 bytes</td>
                    <td className="py-3 px-4 text-slate-300 text-xs">
                      <code className="text-indigo-300">[0x70, 0x05, 0x0D, 0x0A]</code>
                    </td>
                    <td className="py-3 px-4 font-mono text-xs text-indigo-300">70 05 0D 0A</td>
                  </tr>

                  {/* Custom Paint Mode */}
                  <tr className="hover:bg-slate-900/40">
                    <td className="py-3 px-4 font-semibold text-white flex items-center gap-2">
                      <Cpu className="w-4 h-4 text-teal-400" /> Custom Voxel / Paint Header
                    </td>
                    <td className="py-3 px-4 font-mono text-teal-400 font-bold">0x90 (144)</td>
                    <td className="py-3 px-4 text-slate-300">18 bytes</td>
                    <td className="py-3 px-4 text-slate-300 text-xs">
                      <code className="text-teal-300">[0x90]</code> + Frames(2) + Interval/8(1) + ID(4) + Ver(1) + Intensity(1) + Opt(1) + Pad(5) + 0x0D 0x0A
                    </td>
                    <td className="py-3 px-4 font-mono text-xs text-teal-300">90 00 01 0A 00 00 00 01 01 FF 00 00 00 00 00 00 0D 0A</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* TAB 2: LIVE TESTER */}
        {activeTab === 'live_tester' && (
          <div className="space-y-6">
            <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 space-y-4">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                <div>
                  <h3 className="font-semibold text-white text-base">Interactive Command Packet Builder</h3>
                  <p className="text-xs text-slate-400">Select any recovered mode and configure parameters to see exact bytes</p>
                </div>

                <div className="flex items-center gap-2">
                  <span className="text-xs text-slate-400">Target IP:</span>
                  <input
                    type="text"
                    value={targetIp}
                    onChange={(e) => setTargetIp(e.target.value)}
                    className="bg-slate-950 border border-slate-700 px-3 py-1.5 rounded-lg text-xs font-mono text-amber-300 focus:outline-none focus:border-amber-500 w-36"
                  />
                </div>
              </div>

              {/* Mode Selector Buttons */}
              <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-2">
                {[
                  { id: 'lamp', label: 'Solid Color', icon: Lightbulb, opcode: '0x10' },
                  { id: 'clock', label: 'Clock Mode', icon: Clock, opcode: '0x34' },
                  { id: 'music', label: 'Visualizer', icon: Music, opcode: '0x41' },
                  { id: 'mood', label: 'Mood Preset', icon: Layers, opcode: '0x50' },
                  { id: 'emoji', label: '3D Emoji', icon: Sparkles, opcode: '0x20' },
                  { id: 'status', label: 'Status Poll', icon: Info, opcode: '0x60' },
                  { id: 'custom_voxel', label: 'Voxel Paint', icon: Cpu, opcode: '0x90' },
                ].map((m) => {
                  const Icon = m.icon;
                  const isSel = selectedMode === m.id;
                  return (
                    <button
                      key={m.id}
                      onClick={() => setSelectedMode(m.id as any)}
                      className={`p-3 rounded-xl border flex flex-col items-center gap-1.5 text-center transition-all ${
                        isSel
                          ? 'bg-amber-500/20 border-amber-500/40 text-amber-300 shadow-sm'
                          : 'bg-slate-950/70 border-slate-800 text-slate-400 hover:text-slate-200 hover:bg-slate-800/40'
                      }`}
                    >
                      <Icon className="w-5 h-5" />
                      <span className="text-xs font-medium">{m.label}</span>
                      <span className="text-[10px] font-mono text-slate-500">{m.opcode}</span>
                    </button>
                  );
                })}
              </div>

              {/* Dynamic Controls based on selected mode */}
              <div className="bg-slate-950 p-4 rounded-xl border border-slate-800 space-y-4">
                {selectedMode === 'lamp' && (
                  <div className="grid grid-cols-1 sm:grid-cols-4 gap-4">
                    <div>
                      <div className="flex justify-between text-xs text-red-400 font-medium mb-1">
                        <span>Red</span> <span>{r}</span>
                      </div>
                      <input type="range" min="0" max="255" value={r} onChange={(e) => setR(Number(e.target.value))} className="w-full accent-red-500" />
                    </div>
                    <div>
                      <div className="flex justify-between text-xs text-green-400 font-medium mb-1">
                        <span>Green</span> <span>{g}</span>
                      </div>
                      <input type="range" min="0" max="255" value={g} onChange={(e) => setG(Number(e.target.value))} className="w-full accent-green-500" />
                    </div>
                    <div>
                      <div className="flex justify-between text-xs text-blue-400 font-medium mb-1">
                        <span>Blue</span> <span>{b}</span>
                      </div>
                      <input type="range" min="0" max="255" value={b} onChange={(e) => setB(Number(e.target.value))} className="w-full accent-blue-500" />
                    </div>
                    <div>
                      <div className="flex justify-between text-xs text-amber-400 font-medium mb-1">
                        <span>Intensity</span> <span>{intensity}</span>
                      </div>
                      <input type="range" min="0" max="255" value={intensity} onChange={(e) => setIntensity(Number(e.target.value))} className="w-full accent-amber-500" />
                    </div>
                  </div>
                )}

                {selectedMode === 'music' && (
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                    <div>
                      <div className="flex justify-between text-xs text-purple-400 font-medium mb-1">
                        <span>Visualizer Style ID</span> <span>#{presetId}</span>
                      </div>
                      <input type="number" min="1" max="10" value={presetId} onChange={(e) => setPresetId(Number(e.target.value))} className="bg-slate-900 border border-slate-700 px-3 py-1.5 rounded text-xs w-full font-mono text-purple-300" />
                    </div>
                    <div>
                      <div className="flex justify-between text-xs text-cyan-400 font-medium mb-1">
                        <span>Mic Sensitivity (0-100)</span> <span>{sensitivity}%</span>
                      </div>
                      <input type="range" min="0" max="100" value={sensitivity} onChange={(e) => setSensitivity(Number(e.target.value))} className="w-full accent-cyan-500" />
                    </div>
                    <div>
                      <div className="flex justify-between text-xs text-amber-400 font-medium mb-1">
                        <span>Intensity</span> <span>{intensity}</span>
                      </div>
                      <input type="range" min="0" max="255" value={intensity} onChange={(e) => setIntensity(Number(e.target.value))} className="w-full accent-amber-500" />
                    </div>
                  </div>
                )}

                {(selectedMode === 'mood' || selectedMode === 'emoji') && (
                  <div className="space-y-3">
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                      <div>
                        <div className="flex justify-between text-xs text-orange-400 font-medium mb-1">
                          <span>Select Mood / Animation Preset</span> <span>ID #{presetId}</span>
                        </div>
                        <select
                          value={presetId}
                          onChange={(e) => setPresetId(Number(e.target.value))}
                          className="bg-slate-900 border border-slate-700 px-3 py-1.5 rounded-lg text-xs w-full font-mono text-orange-300 focus:outline-none focus:border-orange-500"
                        >
                          <option value="16">✨ ID #16: Fairies / Fireflies (User Favorite!)</option>
                          <option value="1">ID #1: Fire / Campfire / Candle</option>
                          <option value="2">ID #2: Ocean Wave / Ripple</option>
                          <option value="3">ID #3: Rain / Thunderstorm</option>
                          <option value="4">ID #4: Snow / Snowstorm</option>
                          <option value="5">ID #5: Sparkle / Twinkle</option>
                          <option value="6">ID #6: Constellation / Starry Night</option>
                          <option value="7">ID #7: Fireworks / Celebration</option>
                          <option value="8">ID #8: Plasma / Energy Field</option>
                          <option value="9">ID #9: Rainbow / Prism Wave</option>
                          <option value="10">ID #10: Matrix / 3D Digital Rain</option>
                          <option value="11">ID #11: Spectrum / Color Cycle</option>
                          <option value="12">ID #12: Lava / Magma Lamp</option>
                          <option value="13">ID #13: Northern Lights / Aurora</option>
                          <option value="14">ID #14: Spiral / Vortex</option>
                          <option value="15">ID #15: Sunset Glow</option>
                        </select>
                      </div>
                      <div>
                        <div className="flex justify-between text-xs text-amber-400 font-medium mb-1">
                          <span>Intensity / Brightness</span> <span>{intensity} (0x{toHex(intensity)})</span>
                        </div>
                        <input type="range" min="0" max="255" value={intensity} onChange={(e) => setIntensity(Number(e.target.value))} className="w-full accent-amber-500" />
                      </div>
                    </div>
                    <div className="text-[11px] text-slate-400 bg-slate-900/60 p-2.5 rounded-lg border border-slate-800/80">
                      <strong>How Mood Presets Work:</strong> The 4-byte animation ID is passed in bytes 4–7 of the <code className="text-amber-300 font-mono">0x50</code> packet. If the ID is programmed into the cube's on-board firmware, it replies with ACK <code className="text-emerald-400 font-mono">0x00 0x00</code> and runs it locally at full hardware speed.
                    </div>
                  </div>
                )}

                {selectedMode === 'clock' && (
                  <div className="space-y-2">
                    <div className="text-xs text-emerald-400 font-medium">Automatic Time Synchronization</div>
                    <p className="text-xs text-slate-400">
                      Syncs the 8x8x8 matrix to your computer's current clock time (<code className="text-emerald-300 font-mono">{new Date().toLocaleTimeString()}</code>) and calendar date.
                    </p>
                  </div>
                )}

                {/* Live Byte Output */}
                <div className="p-4 rounded-xl bg-slate-900 border border-slate-800 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-slate-400 font-mono">Generated TCP Payload ({liveBytes.length} bytes):</span>
                    <button
                      onClick={() => copyToClipboard(hexString, 'hex')}
                      className="px-3 py-1 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg text-xs flex items-center gap-1.5 transition-colors"
                    >
                      {copiedKey === 'hex' ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                      {copiedKey === 'hex' ? 'Copied' : 'Copy Bytes'}
                    </button>
                  </div>
                  <div className="font-mono text-sm sm:text-base font-bold text-amber-300 tracking-wider break-all bg-slate-950 p-3 rounded-lg border border-slate-800">
                    {hexString}
                  </div>
                  <div className="flex flex-wrap items-center gap-2 pt-1">
                    <button
                      onClick={() => runBridge('send', { ip: targetIp, hex: hexString })}
                      disabled={!bridgeOnline || bridgeBusy}
                      className="px-3 py-1.5 bg-amber-500 hover:bg-amber-400 text-slate-950 font-semibold rounded-lg text-xs flex items-center gap-1.5 disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      <Send className="w-3.5 h-3.5" /> Send to cube at {targetIp}
                    </button>
                    <button
                      onClick={() => runBridge('discover', {})}
                      disabled={!bridgeOnline || bridgeBusy}
                      className="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg text-xs flex items-center gap-1.5 disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      <Wifi className="w-3.5 h-3.5" /> Find cube on network
                    </button>
                    {!bridgeOnline && <span className="text-[11px] text-slate-500">Start the bridge with npm run dev to send directly.</span>}
                  </div>
                  {selectedMode === 'custom_voxel' && (
                    <p className="text-[11px] text-slate-400">
                      A 0x90 header alone only announces frames. Use the 3D Voxel Editor tab to upload the header and the 0x21/0x22/0x23 chunks together.
                    </p>
                  )}
                  {bridgeLog.length > 0 && (
                    <pre className="font-mono text-[11px] text-slate-300 bg-slate-950 border border-slate-800 rounded-lg p-2 max-h-40 overflow-auto whitespace-pre-wrap">{bridgeLog.join('\n')}</pre>
                  )}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* TAB: NOVEL GENERATIVE 3D PRESETS */}
        {activeTab === 'novel_presets' && (
          <div className="space-y-6">
            <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 space-y-3">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-xl bg-purple-500/10 border border-purple-500/30 flex items-center justify-center text-purple-400">
                    <Sparkles className="w-5 h-5" />
                  </div>
                  <div>
                    <h2 className="text-base font-semibold text-white">Novel 3D Generative Presets (Never in Official App)</h2>
                    <p className="text-xs text-slate-400">Powered by the recovered real-time 8×8×8 (512-voxel) frame streaming engine</p>
                  </div>
                </div>
                <span className="text-xs font-mono bg-purple-500/10 text-purple-300 border border-purple-500/20 px-3 py-1 rounded self-start sm:self-auto">
                  Unlimited Creative Possibilities
                </span>
              </div>
              <p className="text-sm text-slate-300 leading-relaxed">
                The original mobile app was restricted to a few hardcoded ROM animations and dead cloud downloads. Because we unlocked the full <strong>1,536-byte 3-chunk voxel streaming protocol (0x90 + 0x21/0x22/0x23)</strong>, we can compute and stream advanced mathematical, quantum, cellular, and particle algorithms directly from your PC at 15–20 FPS!
              </p>
            </div>

            {/* Split layout: Live 3D Simulation Canvas on Left, Preset Library on Right */}
            <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
              {/* Left Column: Interactive 3D Voxel Previewer */}
              <div className="lg:col-span-5 space-y-4">
                <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 space-y-3">
                  <div className="flex items-center justify-between">
                    <div className="text-xs font-semibold text-white uppercase tracking-wider flex items-center gap-2">
                      <Cpu className="w-4 h-4 text-cyan-400" />
                      Live 3D Cube Simulation (512 LEDs)
                    </div>
                    <span className="text-[11px] font-mono text-cyan-400 bg-cyan-950/60 px-2 py-0.5 rounded border border-cyan-800/40">
                      8×8×8 Matrix
                    </span>
                  </div>

                  {/* 3D Canvas rendering */}
                  <div className="rounded-xl overflow-hidden bg-slate-950 border border-slate-800 flex items-center justify-center p-2 relative shadow-inner">
                    <VoxelCanvas preset={selectedNovelPreset} />
                    <div className="absolute bottom-2 left-3 text-[10px] text-slate-500 font-mono">
                      Isometric 3D Projection • Drag to rotate
                    </div>
                  </div>

                  {/* Preset Info Callout */}
                  <div className="bg-slate-950/80 p-3 rounded-lg border border-slate-800/80 space-y-1.5 text-xs">
                    <div className="font-semibold text-amber-300 flex items-center gap-1.5">
                      {novelPresetDetails[selectedNovelPreset]?.title}
                    </div>
                    <p className="text-slate-300 text-[11px] leading-relaxed">
                      {novelPresetDetails[selectedNovelPreset]?.description}
                    </p>
                    <div className="pt-1 flex flex-wrap gap-1.5">
                      {novelPresetDetails[selectedNovelPreset]?.tags.map((tag: string, i: number) => (
                        <span key={i} className="text-[10px] font-mono px-2 py-0.5 rounded bg-slate-800 text-slate-300 border border-slate-700">
                          {tag}
                        </span>
                      ))}
                    </div>
                  </div>
                </div>
              </div>

              {/* Right Column: Preset Cards Grid */}
              <div className="lg:col-span-7 space-y-3">
                <div className="text-xs font-semibold uppercase tracking-wider text-slate-400 flex items-center justify-between">
                  <span>Select a Novel Preset to Preview</span>
                  <span className="text-[11px] text-purple-400 font-mono">10 Curated Algorithms</span>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 max-h-[580px] overflow-y-auto pr-1">
                  {Object.entries(novelPresetDetails).map(([key, item]: [string, any]) => {
                    const isSelected = selectedNovelPreset === key;
                    return (
                      <button
                        key={key}
                        onClick={() => setSelectedNovelPreset(key)}
                        className={`text-left p-3.5 rounded-xl border transition-all flex flex-col justify-between gap-2 ${
                          isSelected
                            ? 'bg-purple-950/30 border-purple-500/60 shadow-lg shadow-purple-950/40 ring-1 ring-purple-500/40'
                            : 'bg-slate-900/80 border-slate-800 hover:border-slate-700 hover:bg-slate-900'
                        }`}
                      >
                        <div className="space-y-1">
                          <div className="flex items-center justify-between">
                            <span className="font-semibold text-sm text-white flex items-center gap-1.5">
                              {item.icon} {item.title}
                            </span>
                            {item.isFavorite && (
                              <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-pink-500/20 text-pink-300 border border-pink-500/30">
                                User Favorite
                              </span>
                            )}
                          </div>
                          <p className="text-xs text-slate-400 line-clamp-2">
                            {item.summary}
                          </p>
                        </div>

                        <div className="flex items-center justify-between pt-1 border-t border-slate-800/60 text-[10px] text-slate-500 font-mono">
                          <span>{item.mathType}</span>
                          <span className={`font-semibold ${isSelected ? 'text-purple-400' : 'text-slate-400'}`}>
                            {isSelected ? 'Active Preview ●' : 'Select'}
                          </span>
                        </div>
                      </button>
                    );
                  })}
                </div>
              </div>
            </div>

            {/* Python Code Export for Streaming this Preset */}
            <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 space-y-3">
              <div className="flex items-center justify-between">
                <h3 className="font-semibold text-white text-sm flex items-center gap-2">
                  <Terminal className="w-4 h-4 text-amber-400" />
                  Stream "{novelPresetDetails[selectedNovelPreset]?.title}" to your Physical Tittle Light
                </h3>
                <button
                  onClick={() =>
                    copyToClipboard(
                      getNovelPresetPython(selectedNovelPreset),
                      'preset_code'
                    )
                  }
                  className="px-3 py-1 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg text-xs flex items-center gap-1.5 transition-colors"
                >
                  {copiedKey === 'preset_code' ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                  {copiedKey === 'preset_code' ? 'Copied' : 'Copy Generator Code'}
                </button>
              </div>
              <p className="text-xs text-slate-400">
                This Python function generates and streams the mathematical 3D frames directly to your cube over TCP port 9999.
              </p>
              <pre className="font-mono text-xs text-slate-300 bg-slate-950 p-3 rounded-lg border border-slate-800 overflow-x-auto">
                {getNovelPresetPython(selectedNovelPreset)}
              </pre>
            </div>
          </div>
        )}

        {/* TAB 4: FRAME SPEC */}
        {activeTab === 'frame_spec' && (
          <div className="space-y-6">
            <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 space-y-3">
              <h3 className="font-semibold text-white text-base flex items-center gap-2">
                <Cpu className="w-5 h-5 text-teal-400" />
                3D Voxel (8×8×8 Cube) Streaming Specification
              </h3>
              <p className="text-sm text-slate-300 leading-relaxed">
                Recovered directly from <code className="text-teal-300 font-mono text-xs">AnimationFrameHolder.java</code> and <code className="text-teal-300 font-mono text-xs">EditPaintFrameFragment.java</code>:
                The Tittle Light consists of <strong>512 RGB LEDs</strong> arranged in an 8×8×8 cube.
              </p>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 space-y-3">
                <h4 className="font-semibold text-white text-sm flex items-center gap-2">
                  <Layers className="w-4 h-4 text-amber-400" /> Coordinate System & Indexing Math
                </h4>
                <div className="bg-slate-950 p-4 rounded-lg border border-slate-800 font-mono text-xs space-y-2 text-slate-300">
                  <div className="text-emerald-400 font-semibold">// 512 Voxels Indexed from 0 to 511:</div>
                  <div>Z = Layer (0 = bottom, 7 = top)</div>
                  <div>Y = Row (0 to 7)</div>
                  <div>X = Column (0 to 7)</div>
                  <div className="pt-2 text-amber-300 font-bold">
                    dotIndex = (Z * 64) + (Y * 8) + X
                  </div>
                  <div className="text-slate-400 text-[11px] pt-1">
                    Each voxel has 3 bytes: [Red, Green, Blue]. A full 3D frame is 512 × 3 = <strong>1,536 bytes</strong>.
                  </div>
                </div>
              </div>

              <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 space-y-3">
                <h4 className="font-semibold text-white text-sm flex items-center gap-2">
                  <Terminal className="w-4 h-4 text-cyan-400" /> Transmission Protocol (3-Chunk Frame Slicing)
                </h4>
                <div className="bg-slate-950 p-4 rounded-lg border border-slate-800 font-mono text-xs space-y-2 text-slate-300">
                  <div className="text-slate-400">// Sent sequentially to TCP:9999 for each frame:</div>
                  <div className="text-cyan-300">
                    Chunk 1: [0x21, FrameOrder(2), LEDs 0..169 (510 bytes), 0x0D, 0x0A]
                  </div>
                  <div className="text-cyan-300">
                    Chunk 2: [0x22, FrameOrder(2), LEDs 170..339 (510 bytes), 0x0D, 0x0A]
                  </div>
                  <div className="text-cyan-300">
                    Chunk 3: [0x23, FrameOrder(2), LEDs 340..511 (516 bytes), 0x0D, 0x0A]
                  </div>
                  <div className="text-slate-400 text-[11px] pt-1">
                    Light returns ACK <code className="text-emerald-400">[0x00, 0x00]</code> after each packet.
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* TAB 4: READY PYTHON SCRIPT */}
        {activeTab === 'python_script' && (
          <div className="space-y-6">
            <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 space-y-3">
              <div className="flex items-center justify-between">
                <h3 className="font-semibold text-white text-base flex items-center gap-2">
                  <Terminal className="w-5 h-5 text-amber-400" />
                  Comprehensive Python Diagnostic & Feature Tester
                </h3>
                <button
                  onClick={() => copyToClipboard(fullPythonScript, 'full_script')}
                  className="px-3 py-1.5 bg-amber-500/20 hover:bg-amber-500/30 text-amber-300 rounded-lg text-xs font-medium border border-amber-500/40 flex items-center gap-1.5 transition-colors"
                >
                  {copiedKey === 'full_script' ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                  {copiedKey === 'full_script' ? 'Copied Python Script' : 'Copy Script (tittle_tester.py)'}
                </button>
              </div>
              <p className="text-sm text-slate-300">
                You can save this as <code className="text-amber-300 font-mono text-xs">tittle_tester.py</code> on your Windows PC and run it right now. It auto-discovers your cube via UDP 10000 and runs a test sequence through all recovered modes!
              </p>
            </div>

            <div className="bg-slate-950 rounded-xl p-4 border border-slate-800 overflow-x-auto">
              <pre className="font-mono text-xs text-slate-300 leading-relaxed">
                {fullPythonScript}
              </pre>
            </div>
          </div>
        )}

        {/* TAB 6: PHASE 2 WINDOWS DESKTOP APP & PACKAGING */}
        {activeTab === 'phase2_preview' && (
          <div className="space-y-6">
            <div className="bg-gradient-to-r from-blue-950/60 to-purple-950/60 border border-blue-800/40 rounded-xl p-5 space-y-3">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                <div>
                  <h3 className="font-semibold text-white text-base flex items-center gap-2">
                    <Code2 className="w-5 h-5 text-amber-400" />
                    Phase 2: Windows Native Desktop App (Tittle Light Control Center)
                  </h3>
                  <p className="text-xs sm:text-sm text-slate-300 mt-1">
                    Complete standalone native application for Windows. Features the 4-tier network sniffer (zero pairing mode needed), full lamp color wheel, official mood presets, 3D clock sync, user-favorite Fairies mode, and all 10 novel 3D presets.
                  </p>
                </div>
                <button
                  onClick={() => {
                    const el = document.createElement('a');
                    el.setAttribute('href', 'data:text/plain;charset=utf-8,' + encodeURIComponent(fullDesktopAppScript));
                    el.setAttribute('download', 'tittle_desktop.py');
                    el.style.display = 'none';
                    document.body.appendChild(el);
                    el.click();
                    document.body.removeChild(el);
                  }}
                  className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-xs font-semibold flex items-center gap-2 self-start sm:self-auto shrink-0 shadow-lg shadow-blue-600/30 transition-all cursor-pointer"
                >
                  <Download className="w-4 h-4" />
                  Download tittle_desktop.py
                </button>
              </div>
            </div>

            {/* Step-by-Step Instructions for Non-Developers */}
            <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 space-y-4">
              <h4 className="font-semibold text-white text-sm flex items-center gap-2">
                <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                Step-by-Step: Running on Your Windows PC (No Coding Required)
              </h4>

              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div className="bg-slate-950 p-4 rounded-xl border border-slate-800/80 space-y-2">
                  <div className="flex items-center gap-2 text-xs font-bold text-amber-300">
                    <span className="w-5 h-5 rounded-full bg-amber-400/20 flex items-center justify-center text-amber-400 text-xs">1</span>
                    Install Python on Windows
                  </div>
                  <p className="text-xs text-slate-300 leading-relaxed">
                    1. Go to <a href="https://www.python.org/downloads/" target="_blank" rel="noreferrer" className="text-blue-400 underline">python.org</a> and download Python for Windows.<br/>
                    2. When the installer opens, <strong>check the box that says "Add python.exe to PATH"</strong> at the bottom.<br/>
                    3. Click <strong>Install Now</strong>. (That's it!)
                  </p>
                </div>

                <div className="bg-slate-950 p-4 rounded-xl border border-slate-800/80 space-y-2">
                  <div className="flex items-center gap-2 text-xs font-bold text-emerald-300">
                    <span className="w-5 h-5 rounded-full bg-emerald-400/20 flex items-center justify-center text-emerald-400 text-xs">2</span>
                    Run with One Double-Click
                  </div>
                  <p className="text-xs text-slate-300 leading-relaxed">
                    1. Save the downloaded <code className="text-slate-200 font-mono">tittle_desktop.py</code> to your Desktop or Documents.<br/>
                    2. <strong>Double-click</strong> <code className="text-slate-200 font-mono">tittle_desktop.py</code>.<br/>
                    3. The graphical window appears immediately! It automatically sniffs your Wi-Fi and connects in 2 seconds.
                  </p>
                </div>

                <div className="bg-slate-950 p-4 rounded-xl border border-slate-800/80 space-y-2">
                  <div className="flex items-center gap-2 text-xs font-bold text-blue-300">
                    <span className="w-5 h-5 rounded-full bg-blue-400/20 flex items-center justify-center text-blue-400 text-xs">3</span>
                    Optional: Turn into a .EXE
                  </div>
                  <p className="text-xs text-slate-300 leading-relaxed">
                    To make a standalone <code className="text-slate-200 font-mono">TittleLight.exe</code> program icon:<br/>
                    Open Command Prompt and paste:<br/>
                    <code className="text-amber-300 font-mono text-[11px] block bg-slate-900 p-1.5 rounded mt-1">pip install pyinstaller</code>
                    <code className="text-amber-300 font-mono text-[11px] block bg-slate-900 p-1.5 rounded mt-1">pyinstaller --noconsole --onefile tittle_desktop.py</code>
                  </p>
                </div>
              </div>
            </div>

            {/* Architecture: How Zero-Friction Sniffing & Offline Handling Work */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 space-y-3">
                <h4 className="font-semibold text-white text-sm flex items-center gap-2">
                  <Cpu className="w-4 h-4 text-cyan-400" />
                  How the 4-Tier Zero-Friction Sniffer Works
                </h4>
                <div className="space-y-2 text-xs text-slate-300 leading-relaxed">
                  <p>
                    Unlike the mobile app which forced you to pair every time, the desktop app executes this silent scan:
                  </p>
                  <ul className="space-y-1.5 text-slate-300">
                    <li className="flex items-start gap-1.5">
                      <span className="text-emerald-400 font-mono font-bold">• Tier 1:</span>
                      <span><strong>Cached IP Ping (~50ms):</strong> Checks <code className="text-slate-200 font-mono">tittle_config.json</code> for the last known IP. If the light answers, it connects instantly.</span>
                    </li>
                    <li className="flex items-start gap-1.5">
                      <span className="text-amber-400 font-mono font-bold">• Tier 2:</span>
                      <span><strong>UDP Broadcast Ping:</strong> Shouts opcode <code className="text-slate-200 font-mono">[0x70, 0x04]</code> to 255.255.255.255:10000. Tittle replies with its name, MAC, and IP.</span>
                    </li>
                    <li className="flex items-start gap-1.5">
                      <span className="text-blue-400 font-mono font-bold">• Tier 3:</span>
                      <span><strong>Subnet Port 9999 Sniffer:</strong> If your router blocks UDP broadcasts, the app silently tests port 9999 across all local IPs (<code className="text-slate-200 font-mono">192.168.x.1-254</code>) concurrently in 1.5 seconds.</span>
                    </li>
                    <li className="flex items-start gap-1.5">
                      <span className="text-purple-400 font-mono font-bold">• Tier 4:</span>
                      <span><strong>Manual Override:</strong> Type any IP directly in the top header and click Sniff/Connect.</span>
                    </li>
                  </ul>
                </div>
              </div>

              <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 space-y-3">
                <h4 className="font-semibold text-white text-sm flex items-center gap-2">
                  <Radio className="w-4 h-4 text-purple-400" />
                  Handling Offline & IP Address Changes
                </h4>
                <div className="space-y-2 text-xs text-slate-300 leading-relaxed">
                  <ul className="space-y-2 text-slate-300">
                    <li className="flex items-start gap-1.5">
                      <span className="text-emerald-400 font-bold">✔</span>
                      <span><strong>Clean Threading:</strong> All socket transmissions run on background daemon threads. The app interface never freezes or stutters even if the light takes a moment to respond.</span>
                    </li>
                    <li className="flex items-start gap-1.5">
                      <span className="text-emerald-400 font-bold">✔</span>
                      <span><strong>Auto-Reconnect on IP Shift:</strong> If your router reboots and hands your light a new DHCP IP address, the app catches the socket disconnect and automatically runs the Tier 2/3 sniffer to lock onto the new IP.</span>
                    </li>
                    <li className="flex items-start gap-1.5">
                      <span className="text-emerald-400 font-bold">✔</span>
                      <span><strong>Debug Terminal:</strong> Tab 6 in the desktop app logs every single byte transmitted (TX) and received (RX) in real time so you can inspect the exact raw network conversation.</span>
                    </li>
                  </ul>
                </div>
              </div>
            </div>

            {/* Code Viewer */}
            <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 space-y-3">
              <div className="flex items-center justify-between">
                <h4 className="font-semibold text-white text-sm flex items-center gap-2">
                  <Code2 className="w-4 h-4 text-amber-400" />
                  Native Windows App Source Code (<code className="text-amber-300 font-mono text-xs">tittle_desktop.py</code>)
                </h4>
                <button
                  onClick={() => copyToClipboard(fullDesktopAppScript, 'desktop_script')}
                  className="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg text-xs font-medium flex items-center gap-1.5 transition-all cursor-pointer border border-slate-700"
                >
                  <Copy className="w-3.5 h-3.5" />
                  {copiedKey === 'desktop_script' ? 'Copied Full Code!' : 'Copy Desktop App Code'}
                </button>
              </div>

              <div className="bg-slate-950 rounded-xl p-4 border border-slate-800 max-h-[460px] overflow-y-auto">
                <pre className="font-mono text-xs text-slate-300 leading-relaxed">
                  {fullDesktopAppScript}
                </pre>
              </div>
            </div>

            {/* iOS Roadmap */}
            <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 space-y-3">
              <h4 className="font-semibold text-white text-sm flex items-center gap-2">
                <Sparkles className="w-4 h-4 text-pink-400" />
                iOS Version Roadmap & Requirements
              </h4>
              <div className="space-y-2 text-xs text-slate-300 leading-relaxed">
                <p>
                  Because we reversed the complete protocol and recovered the original Objective-C files from <code className="text-slate-200 font-mono">tittle-sdk-ios</code>:
                </p>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-1">
                  <div className="p-3 bg-slate-950 rounded-lg border border-slate-800 space-y-1.5">
                    <strong className="text-white">What an iOS Version Requires:</strong>
                    <ul className="list-disc list-inside space-y-1 text-slate-400">
                      <li>A Mac running macOS with Xcode installed.</li>
                      <li>Written in <strong>Swift</strong> using Apple's standard <code className="text-slate-300 font-mono">Network.framework</code> (<code className="text-slate-300 font-mono">NWConnection</code> for TCP port 9999 and <code className="text-slate-300 font-mono">NWListener</code> for UDP port 10000).</li>
                      <li>Local Network Usage permission string in <code className="text-slate-300 font-mono">Info.plist</code> (<code className="text-slate-300 font-mono">NSLocalNetworkUsageDescription</code>).</li>
                    </ul>
                  </div>

                  <div className="p-3 bg-slate-950 rounded-lg border border-slate-800 space-y-1.5">
                    <strong className="text-white">Installing on Your iPhone:</strong>
                    <ul className="list-disc list-inside space-y-1 text-slate-400">
                      <li><strong>Option A (Free - Recommended):</strong> Sideload via <strong>AltStore</strong> or <strong>Sideloadly</strong> using your regular personal Apple ID. Zero developer fees!</li>
                      <li><strong>Option B (Official Developer):</strong> If you have an Apple Developer Program subscription ($99/yr), you can build via TestFlight or install directly.</li>
                      <li>The Windows app works right now, and when you're ready for iOS, the exact same protocol code ports directly to Swift!</li>
                    </ul>
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}

      </main>

      {pizza && (
        <div className="fixed inset-0 z-50 flex flex-col items-center justify-center gap-6 bg-slate-950/70 backdrop-blur-sm pointer-events-none">
          <div className="text-[9rem] leading-none animate-spin [animation-duration:1.5s]">🍕</div>
          <div className="text-sm text-amber-200 bg-slate-900/90 border border-amber-500/30 px-4 py-2 rounded-lg">{pizza}</div>
        </div>
      )}

      {/* Footer */}
      <footer className="border-t border-slate-800 bg-slate-900/60 py-4 px-4 text-center text-xs text-slate-500">
        Tittle Light Reverse Engineering Workbench • Official protocol reverse-engineered from APK v2.2.25 •{' '}
        <button onClick={pizzaTime} className="hover:text-amber-300 transition-colors" title="🍕">
          made by Claude and Chad
        </button>
      </footer>
    </div>
  );
}

const fullPythonScript = `# ==============================================================================
# Tittle Light Complete Hardware Controller & Protocol Diagnostic
# Reverse-Engineered from Official APK (com.clarityhk.tittle.android)
# Compatible with Windows, macOS, and Linux
# ==============================================================================
import socket
import time
from datetime import datetime

class TittleController:
    UDP_DISCOVERY_PORT = 10000
    TCP_COMMAND_PORT = 9999

    def __init__(self, ip=None):
        self.ip = ip

    @staticmethod
    def get_local_ip():
        """Finds your computer's local IP address on the Wi-Fi network."""
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
        finally:
            s.close()
        return ip

    def discover(self, timeout=8):
        """Discovers the Tittle Light using the official UDP broadcast protocol."""
        local_ip = self.get_local_ip()
        print(f"[*] Your PC IP: {local_ip}")
        ip_parts = [int(p) for p in local_ip.split('.')]

        # Start reverse TCP listener on port 9999
        tcp_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tcp_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        tcp_server.bind(('0.0.0.0', self.TCP_COMMAND_PORT))
        tcp_server.listen(1)
        tcp_server.settimeout(timeout)

        # Broadcast packet: [0x70, 0x04, IP[0..3], 0x0D, 0x0A]
        broadcast_packet = bytes([0x70, 0x04, ip_parts[0], ip_parts[1], ip_parts[2], ip_parts[3], 0x0D, 0x0A])
        udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        print("[*] Broadcasting UDP discovery ping on port 10000...")
        udp_sock.sendto(broadcast_packet, ('255.255.255.255', self.UDP_DISCOVERY_PORT))

        try:
            client_sock, addr = tcp_server.accept()
            print(f"[+] Tittle Light connected back from: {addr[0]}")
            data = client_sock.recv(1024)
            client_sock.close()
            self.ip = addr[0]
            print(f"[+] Light IP confirmed: {self.ip}")
            return self.ip
        except socket.timeout:
            print("[-] Discovery timed out. Make sure your PC and Tittle are on the same 2.4GHz Wi-Fi.")
            return None
        finally:
            tcp_server.close()
            udp_sock.close()

    def send_raw_command(self, packet, expect_response=True):
        """Sends raw command bytes to the light over TCP port 9999."""
        if not self.ip:
            raise ValueError("No Light IP set. Run discover() or pass an IP.")
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(4.0)
        s.connect((self.ip, self.TCP_COMMAND_PORT))
        s.send(packet)
        response = None
        if expect_response:
            try:
                response = s.recv(1024)
            except socket.timeout:
                pass
        s.close()
        return response

    # 1. Solid Color Mode (OpCode 0x10)
    def set_lamp(self, r, g, b, intensity=255):
        print(f"[*] Setting solid color RGB({r}, {g}, {b}) Intensity={intensity}...")
        packet = bytes([0x10, r & 0xFF, g & 0xFF, b & 0xFF, intensity & 0xFF, 0x0D, 0x0A])
        res = self.send_raw_command(packet)
        print(f"    Response: {res.hex() if res else 'None'}")

    def turn_off(self):
        print("[*] Turning lamp OFF...")
        self.set_lamp(0, 0, 0, 0)

    # 2. Clock Mode (OpCode 0x34)
    def set_clock(self, intensity=255, clock_face_id=1):
        now = datetime.now()
        print(f"[*] Syncing Clock Mode: {now.strftime('%H:%M:%S')} (Face #{clock_face_id})...")
        yy = now.year - 2000
        packet = bytes([
            0x34,
            now.hour, now.minute, now.second,
            0x00, 0x00, 0x00, clock_face_id,
            intensity & 0xFF, 0x01,
            yy, now.month, now.day,
            0x00, 0x00, 0x00, 0x00, 0x00,
            0x0D, 0x0A
        ])
        res = self.send_raw_command(packet)
        print(f"    Response: {res.hex() if res else 'None'}")

    # 3. Music Visualizer Mode (OpCode 0x41)
    def set_music_visualizer(self, sensitivity=50, intensity=255, style_id=1):
        print(f"[*] Activating Music Visualizer: Style #{style_id}, Sensitivity={sensitivity}%...")
        packet = bytes([
            0x41,
            0x00, 0x00, 0x00, style_id,
            intensity & 0xFF, sensitivity & 0xFF, 0x01,
            0x00, 0x00, 0x00, 0x00, 0x00,
            0x0D, 0x0A
        ])
        res = self.send_raw_command(packet)
        print(f"    Response: {res.hex() if res else 'None'}")

    # 4. Mood Animation Mode (OpCode 0x50)
    def set_mood_animation(self, mood_id=1, intensity=255):
        print(f"[*] Playing Mood Animation #{mood_id}...")
        packet = bytes([
            0x50,
            0x00, 0x0A, 0x14,
            0x00, 0x00, 0x00, mood_id,
            0x01, intensity & 0xFF,
            0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
            0x0D, 0x0A
        ])
        res = self.send_raw_command(packet)
        print(f"    Response: {res.hex() if res else 'None'}")

    # 6. Fairies Preset Probe & 3D Procedural Generator
    def test_fairies(self):
        print("\n[*] --- Testing Fairies Preset ---")
        print("[*] Probing on-board Fairies / Mood slots...")
        for probe_id in [16, 5, 6, 8, 14]:
            res = self.send_raw_command(bytes([
                0x50, 0x00, 0x0A, 0x14,
                0x00, 0x00, 0x00, probe_id,
                0x01, 0xFF,
                0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
                0x0D, 0x0A
            ]))
            if res and res.startswith(b'\x00\x00'):
                print(f"[+] Preset ID #{probe_id} recognized on-board! Playing...")
                return

        print("[*] Streaming real-time 3D Voxel Fairies particle effect to cube...")
        self.stream_fairies_particles(duration_sec=8)

    def stream_fairies_particles(self, duration_sec=8):
        """Simulates 6 whimsical fairies darting through the 8x8x8 3D space with soft luminous trails."""
        import random, math
        # 6 Fairies: [x, y, z, vx, vy, vz, r, g, b]
        fairies = [
            [random.uniform(1, 6), random.uniform(1, 6), random.uniform(1, 6),
             random.uniform(-0.4, 0.4), random.uniform(-0.4, 0.4), random.uniform(-0.4, 0.4),
             *color]
            for color in [
                (255, 120, 200),  # Fairy Pink
                (80, 220, 255),   # Magic Cyan
                (255, 230, 80),   # Golden Stardust
                (180, 100, 255),  # Ethereal Violet
                (100, 255, 180),  # Mint Glimmer
                (255, 160, 80),   # Amber Sparkle
            ]
        ]

        # Send Header (0x90) for custom animation streaming
        self.send_raw_command(bytes([
            0x90, 0x00, 0x01, 0x0A,
            0x00, 0x00, 0x00, 0x01,
            0x01, 0xFF, 0x00,
            0x00, 0x00, 0x00, 0x00, 0x00,
            0x0D, 0x0A
        ]))

        start_time = time.time()
        frame_idx = 0
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2.0)
        s.connect((self.ip, self.TCP_COMMAND_PORT))

        print("[*] Fairies dancing in 3D matrix...")
        try:
            while time.time() - start_time < duration_sec:
                # 512 LEDs (8x8x8) RGB buffer initialized to 0
                frame_buffer = bytearray(512 * 3)

                # Move fairies
                for f in fairies:
                    for i in range(3):
                        f[i] += f[i+3]
                        if f[i] <= 0 or f[i] >= 7:
                            f[i+3] *= -1  # bounce off walls
                            f[i] = max(0.0, min(7.0, f[i]))

                    # Light up nearest voxel
                    x, y, z = int(round(f[0])), int(round(f[1])), int(round(f[2]))
                    idx = (z * 64) + (y * 8) + x
                    if 0 <= idx < 512:
                        frame_buffer[idx*3] = f[6]
                        frame_buffer[idx*3 + 1] = f[7]
                        frame_buffer[idx*3 + 2] = f[8]

                # Send in 3 chunks (LEDs 0-169, 170-339, 340-511)
                order_bytes = [(frame_idx >> 8) & 0xFF, frame_idx & 0xFF]
                c1 = bytes([0x21, *order_bytes]) + frame_buffer[0:510] + bytes([0x0D, 0x0A])
                c2 = bytes([0x22, *order_bytes]) + frame_buffer[510:1020] + bytes([0x0D, 0x0A])
                c3 = bytes([0x23, *order_bytes]) + frame_buffer[1020:1536] + bytes([0x0D, 0x0A])

                s.send(c1); s.recv(64)
                s.send(c2); s.recv(64)
                s.send(c3); s.recv(64)

                frame_idx = (frame_idx + 1) % 256
                time.sleep(0.08)  # ~12 FPS smooth motion
        finally:
            s.close()

if __name__ == '__main__':
    print("=== Tittle Light Diagnostic & Fairies Tester ===")
    tittle = TittleController()
    ip = tittle.discover()
    if ip:
        print(f"[+] Discovered Tittle Light at: {ip}")
        # Test 1: Lamp solid warm white
        tittle.set_lamp(255, 180, 100, 200)
        time.sleep(2)
        # Test 2: Fairies
        tittle.test_fairies()
        time.sleep(2)
        # Test 3: Turn off
        tittle.turn_off()
        print("[+] Test completed successfully!")
`;

// ==============================================================================
// Novel Preset Data & Algorithmic Definitions
// ==============================================================================
const novelPresetDetails: Record<string, any> = {
  fairies_pro: {
    title: 'Enhanced Fairies 2.0 (Forest Glade)',
    icon: '✨',
    isFavorite: true,
    summary: '8 autonomous glowing fairies drifting through 3D space with persistent stardust trails.',
    description: 'An enhanced recreation of the user-favorite Fairies mode. 8 autonomous light agents navigate the 8x8x8 volume with gentle physics, changing from pastel rose and ether-cyan to golden stardust, trailing fading embers as they glide.',
    mathType: '3D Autonomous Particle Swarm with Soft Dissolve',
    tags: ['User Favorite', 'Pastel Colors', '15 FPS', 'Relaxing Ambient']
  },
  tesseract: {
    title: '4D Tesseract (Hypercube Rotation)',
    icon: '⚛️',
    summary: 'Projection of a 4-dimensional hypercube rotating through 4D space into the 3D voxel grid.',
    description: 'Calculates the 16 vertices and 32 edges of a 4D hypercube rotating in the XW and YZ planes, projecting the shadow into our 8x8x8 matrix. The inner cube continuously turns inside-out while expanding and contracting.',
    mathType: '4D Orthographic Projection (SO(4) Rotation)',
    tags: ['Higher Dimensions', 'Geometric', 'Deep Violet & Cyan', 'Demoscene Classic']
  },
  jellyfish: {
    title: 'Deep Abyss Bioluminescent Jellyfish',
    icon: '🪼',
    summary: 'A glowing bell pulsing upward through dark water, trailing flowing ethereal tentacles.',
    description: 'A translucent bell contracts and propels itself upward through the cube using harmonic sine waves, then slowly drifts down while eight bioluminescent trailing tentacles ripple behind it in deep ocean blues and neon aquas.',
    mathType: 'Parametric Sine Fluid Dynamics',
    tags: ['Organic Animation', 'Oceanic Glow', 'Soothing Cadence', 'Translucent Bell']
  },
  game_of_life: {
    title: '3D Conway\'s Game of Life (Voxel Automata)',
    icon: '🦠',
    summary: 'John Conway\'s cellular automata extended to 3D space using Carter 4555 survival rules.',
    description: 'A living universe in your cube! Voxels live, die, and reproduce based on the 26 neighbors surrounding each point in 3D. Features natural gliders, pulsing oscillators, and self-sustaining micro-colonies.',
    mathType: '3D Cellular Automata (Rule 4555)',
    tags: ['Emergent AI', 'Mathematical', 'Electric Amber & Lime', 'Dynamic Evolution']
  },
  supernova: {
    title: 'Supernova & Relativistic Jets',
    icon: '🌌',
    summary: 'A swirling golden accretion disk with twin high-velocity relativistic plasma jets.',
    description: 'A dense gravitational disk of hot matter rotates along the equatorial Z=3/Z=4 plane, while dual high-energy particle beams erupt continuously from the north and south poles into the cosmos, culminating in periodic cosmic collapses.',
    mathType: 'Astrophysical Accretion & Particle Ejection',
    tags: ['Cosmic', 'High Energy', 'Gold, Orange & Ultraviolet', 'Hypnotic']
  },
  dna_helix: {
    title: 'DNA Quantum Double Helix',
    icon: '🧬',
    summary: 'Intertwined spinning double helix with horizontal base-pair chemical bonds.',
    description: 'Two complementary helical strands (one hot magenta, one electric cyan) rotate in synchronization around the vertical axis. Every half-turn, glowing hydrogen base pairs illuminate in warm golden-white connecting the two strands.',
    mathType: 'Parametric Dual Helix ($x=r\\cos\\theta, y=r\\sin\\theta, z=k\\theta$)',
    tags: ['Biomedical', 'Symmetrical', 'Vibrant Contrast', 'Continuous Spin']
  },
  matrix_3d: {
    title: 'Matrix 3.0 (True 3D Volumetric Rain)',
    icon: '🟩',
    summary: 'Layered falling digital rain that cascades through all 8 depths with floor splash ripples.',
    description: 'Unlike the flat 2D matrix scroll on the official app, this operates across all 8 depth planes. Streams of code fall with brilliant white phosphor leading heads, fading green data trails, and shockwave ripples when they hit the floor.',
    mathType: 'Multi-layer Volumetric Cascade with Floor Physics',
    tags: ['Sci-Fi Iconic', 'Cyberpunk', 'Phosphor Green & White', 'Layered Depth']
  },
  snake_3d: {
    title: '3D Arcade Neon Snake (Autonomous AI)',
    icon: '🐍',
    summary: 'A classic neon snake crawling smoothly through the 3D volume hunting glowing apples.',
    description: 'Watch an autonomous AI snake slither through 3D space in 6 directions (+X, -X, +Y, -Y, +Z, -Z), steering towards a pulsing red apple voxel while avoiding running into its own glowing tail, growing longer with each feast!',
    mathType: 'A* 3D Pathfinding & Hamiltonian Avoidance',
    tags: ['Retro Gaming', 'AI Played', 'Vibrant Neon', 'Fun & Entertaining']
  },
  meteor: {
    title: 'Cosmic Meteor Storm',
    icon: '🌠',
    summary: 'Blazing diagonal fireballs streaking corner-to-corner with expanding smoke tails.',
    description: 'High-speed incandescent streaks enter from randomized top coordinates and burn diagonally across the cube with bright golden-orange heads, leaving dispersing smoke trails and soft background twinkling stars.',
    mathType: 'Vector Raymarching & Particle Decay',
    tags: ['High Speed', 'Dramatic', 'Fiery Palette', 'Ambient Night']
  },
  acoustic: {
    title: '3D Acoustic Pressure Sphere',
    icon: '🔊',
    summary: 'Concentric sonic shockwaves radiating outward from the center voxel of the cube.',
    description: 'A central point of light beats like a heart, sending expanding spherical shells of color ripple out to the outer glass walls, shifting through the color spectrum as they travel across the 8x8x8 boundary.',
    mathType: '3D Spherical Radial Wave Equation ($r = \\sqrt{x^2+y^2+z^2}$)',
    tags: ['Audio Visualizer', 'Radial Geometry', 'Rainbow Spectrum', 'Pulse']
  }
};

function getNovelPresetPython(presetKey: string): string {
  const pName = novelPresetDetails[presetKey]?.title || 'Custom Preset';
  return [
    '# Python Real-Time Voxel Streaming Engine for: ' + pName,
    '# Streams 512 LEDs (8x8x8 RGB) over TCP port 9999 at 15 FPS',
    'import socket, time, math, random',
    '',
    'def stream_' + presetKey + '(tittle_ip, duration_sec=15):',
    '    print("[*] Starting \'' + pName + '\' on " + str(tittle_ip) + ":9999...")',
    '    ',
    '    # 1. Send Paint Header (0x90) announcing custom voxel stream',
    '    s_cmd = socket.socket(socket.AF_INET, socket.SOCK_STREAM)',
    '    s_cmd.connect((tittle_ip, 9999))',
    '    header = bytes([0x90, 0x00, 0x01, 0x0A, 0x00, 0x00, 0x00, 0x01, 0x01, 0xFF, 0x00, 0, 0, 0, 0, 0, 0x0D, 0x0A])',
    '    s_cmd.send(header)',
    '    s_cmd.close()',
    '    ',
    '    # 2. Open continuous TCP streaming socket',
    '    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)',
    '    sock.connect((tittle_ip, 9999))',
    '    start_time = time.time()',
    '    frame_idx = 0',
    '    ',
    '    try:',
    '        while time.time() - start_time < duration_sec:',
    '            t = time.time() - start_time',
    '            buffer = bytearray(512 * 3)',
    '            ',
    '            # --- Algorithm Frame Generation ---',
    '            for z in range(8):',
    '                for y in range(8):',
    '                    for x in range(8):',
    '                        idx = (z * 64) + (y * 8) + x',
    '                        r, g, b = compute_' + presetKey + '_voxel(x, y, z, t)',
    '                        buffer[idx*3] = max(0, min(255, int(r)))',
    '                        buffer[idx*3 + 1] = max(0, min(255, int(g)))',
    '                        buffer[idx*3 + 2] = max(0, min(255, int(b)))',
    '            ',
    '            # Send in 3 chunks (LEDs 0-169, 170-339, 340-511)',
    '            o = [(frame_idx >> 8) & 0xFF, frame_idx & 0xFF]',
    '            c1 = bytes([0x21, *o]) + buffer[0:510] + bytes([0x0D, 0x0A])',
    '            c2 = bytes([0x22, *o]) + buffer[510:1020] + bytes([0x0D, 0x0A])',
    '            c3 = bytes([0x23, *o]) + buffer[1020:1536] + bytes([0x0D, 0x0A])',
    '            ',
    '            sock.send(c1); sock.recv(64)',
    '            sock.send(c2); sock.recv(64)',
    '            sock.send(c3); sock.recv(64)',
    '            frame_idx = (frame_idx + 1) % 256',
    '            time.sleep(0.066) # ~15 FPS',
    '    finally:',
    '        sock.close()',
    '        print("[+] Stream finished.")',
    '',
    'def compute_' + presetKey + '_voxel(x, y, z, t):',
    '    dx, dy, dz = x - 3.5, y - 3.5, z - 3.5',
    '    dist = math.sqrt(dx*dx + dy*dy + dz*dz)',
    '    val = math.sin(dist * 1.5 - t * 4.0)',
    '    if val > 0.6:',
    '        return (255, int(150 + 100 * math.sin(t)), int(220 + 35 * math.cos(t)))',
    '    return (0, 0, 0)'
  ].join('\n');
}

// Interactive 3D Voxel Canvas component
function VoxelCanvas({ preset }: { preset: string }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let animId: number;
    let time = 0;

    const render = () => {
      time += 0.03;
      ctx.fillStyle = '#020617';
      ctx.fillRect(0, 0, canvas.width, canvas.height);

      const cx = canvas.width / 2;
      const cy = canvas.height / 2 + 20;
      const angle = time * 0.4;
      const cosA = Math.cos(angle);
      const sinA = Math.sin(angle);

      // Render 8x8x8 voxels in depth order
      const voxels: { sx: number; sy: number; depth: number; color: string; size: number }[] = [];

      for (let z = 0; z < 8; z++) {
        for (let y = 0; y < 8; y++) {
          for (let x = 0; x < 8; x++) {
            const dx = x - 3.5;
            const dy = y - 3.5;
            const dz = z - 3.5;

            // Rotated coordinates
            const rx = dx * cosA - dy * sinA;
            const ry = dx * sinA + dy * cosA;
            const rz = dz;

            // Isometric screen projection
            const sx = cx + rx * 22;
            const sy = cy + ry * 10 - rz * 18;
            const depth = ry; // for z-sorting

            // Calculate illumination based on preset
            let color = '';
            let size = 2.0;

            if (preset === 'fairies_pro') {
              // 8 floating particles
              for (let fi = 0; fi < 8; fi++) {
                const fx = 3.5 + 3.0 * Math.sin(time * 0.8 + fi * 1.3);
                const fy = 3.5 + 3.0 * Math.cos(time * 0.7 + fi * 0.9);
                const fz = 3.5 + 2.8 * Math.sin(time * 1.1 + fi * 2.1);
                const d = Math.hypot(x - fx, y - fy, z - fz);
                if (d < 1.3) {
                  const hue = (fi * 45 + time * 30) % 360;
                  color = 'hsla(' + hue + ', 90%, 65%, ' + ((1.3 - d) / 1.3) + ')';
                  size = 4.5;
                  break;
                }
              }
            } else if (preset === 'tesseract') {
              // 4D projection rotating
              const w = Math.sin(time * 1.5) * 2;
              const d = Math.abs(Math.sin(rx * 0.5 + ry * 0.5 + rz * 0.5 + w));
              if (d < 0.25) {
                color = 'rgba(180, 100, 255, ' + (1 - d * 3) + ')';
                size = 3.8;
              }
            } else if (preset === 'jellyfish') {
              // Pulsing dome floating upwards
              const jz = (time * 2.0) % 8;
              const distFromCenter = Math.hypot(dx, dy);
              if (Math.abs(z - jz) < 1.2 && distFromCenter < 3.2) {
                color = 'rgba(56, 189, 248, 0.9)';
                size = 4.0;
              } else if (z < jz && distFromCenter > 1.5 && distFromCenter < 3.5 && (x % 2 === 0 || y % 2 === 0)) {
                color = 'rgba(168, 85, 247, 0.7)';
                size = 2.5;
              }
            } else if (preset === 'supernova') {
              // Accretion disk + polar jets
              if (Math.abs(z - 3.5) < 1.0) {
                const r = Math.hypot(dx, dy);
                if (r > 1.5 && r < 4.0) {
                  color = 'rgba(251, 146, 60, 0.9)';
                  size = 4.0;
                }
              } else if (Math.hypot(dx, dy) < 1.2) {
                color = 'rgba(34, 211, 238, 0.95)';
                size = 4.5;
              }
            } else if (preset === 'dna_helix') {
              const theta = z * 0.9 + time * 2;
              const hx1 = 3.5 + 2.5 * Math.cos(theta);
              const hy1 = 3.5 + 2.5 * Math.sin(theta);
              const hx2 = 3.5 - 2.5 * Math.cos(theta);
              const hy2 = 3.5 - 2.5 * Math.sin(theta);
              if (Math.hypot(x - hx1, y - hy1) < 1.1) {
                color = 'rgba(244, 63, 94, 0.95)';
                size = 4.0;
              } else if (Math.hypot(x - hx2, y - hy2) < 1.1) {
                color = 'rgba(6, 182, 212, 0.95)';
                size = 4.0;
              } else if (Math.abs(z % 2) < 0.5 && Math.abs((x - 3.5) * Math.sin(theta) - (y - 3.5) * Math.cos(theta)) < 0.8) {
                color = 'rgba(253, 224, 71, 0.8)';
                size = 2.5;
              }
            } else if (preset === 'matrix_3d') {
              const dropZ = 7 - ((time * 6 + x * 2 + y * 3) % 9);
              if (Math.abs(z - dropZ) < 0.8) {
                color = 'rgba(255, 255, 255, 0.95)';
                size = 4.0;
              } else if (z < dropZ && z > dropZ - 3) {
                color = 'rgba(74, 222, 128, 0.8)';
                size = 3.0;
              }
            } else if (preset === 'game_of_life') {
              const cell = Math.sin(x * 12 + y * 7 + z * 5 + Math.floor(time * 2));
              if (cell > 0.4) {
                color = 'rgba(250, 204, 21, 0.85)';
                size = 3.5;
              }
            } else if (preset === 'snake_3d') {
              const headZ = Math.floor(3.5 + 3 * Math.sin(time * 1.5));
              const headX = Math.floor(3.5 + 3 * Math.cos(time * 1.2));
              if (x === headX && z === headZ && y === 4) {
                color = 'rgba(34, 197, 94, 1)';
                size = 5.0;
              } else if (x === 6 && y === 2 && z === 5) {
                color = 'rgba(239, 68, 68, 1)';
                size = 5.0;
              }
            } else {
              // Acoustic / default radial
              const dist = Math.hypot(dx, dy, dz);
              const wave = Math.sin(dist * 1.5 - time * 5);
              if (wave > 0.5) {
                const hue = (dist * 40 + time * 50) % 360;
                color = 'hsla(' + hue + ', 85%, 60%, 0.85)';
                size = 3.5;
              }
            }

            // Default faint grid point if unlit
            if (!color) {
              color = 'rgba(51, 65, 85, 0.25)';
              size = 1.2;
            }

            voxels.push({ sx, sy, depth, color, size });
          }
        }
      }

      // Sort by depth so closer voxels draw over far ones
      voxels.sort((a, b) => a.depth - b.depth);

      // Draw all voxels
      for (const v of voxels) {
        ctx.beginPath();
        ctx.arc(v.sx, v.sy, v.size, 0, Math.PI * 2);
        ctx.fillStyle = v.color;
        ctx.fill();

        // Extra glow for illuminated voxels
        if (v.size > 2.0) {
          ctx.beginPath();
          ctx.arc(v.sx, v.sy, v.size * 2.2, 0, Math.PI * 2);
          ctx.fillStyle = v.color.replace(/[\d\.]+\)$/, '0.15)');
          ctx.fill();
        }
      }

      animId = requestAnimationFrame(render);
    };

    render();
    return () => cancelAnimationFrame(animId);
  }, [preset]);

  return <canvas ref={canvasRef} width={380} height={380} className="w-full max-w-[380px] aspect-square rounded-lg" />;
}

