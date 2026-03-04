// HermitCrab Web Chat - Nostr client
// Uses NIP-07 for login and NIP-04 for encrypted messaging

const CONFIG = {
    RELAYS: [
        'wss://relay.damus.io',
        'wss://relay.nostr.band',
        'wss://nos.lol',
        'wss://relay.nostrcheck.me'
    ],
    CRAB_PUBKEY: null, // Set by configuration or discovery
    DM_KIND: 4,
    METADATA_KIND: 0
};

// Check for HermitCrab pubkey in URL params or localStorage
const urlParams = new URLSearchParams(window.location.search);
CONFIG.CRAB_PUBKEY = urlParams.get('crab') || localStorage.getItem('hermitcrab_pubkey') || '0000000000000000000000000000000000000000000000000000000000000000';

class NostrClient {
    constructor() {
        this.relays = new Map();
        this.pubkey = null;
        this.connectionStatus = 'disconnected';
        this.messageHandlers = [];
        this.subscriptions = new Map();
    }

    async checkExtension() {
        return typeof window.nostr !== 'undefined';
    }

    async getPublicKey() {
        if (!await this.checkExtension()) {
            throw new Error('No Nostr extension found. Please install Alby, nos2x, or similar.');
        }
        this.pubkey = await window.nostr.getPublicKey();
        return this.pubkey;
    }

    async encryptMessage(recipientPubkey, message) {
        if (!window.nostr.nip04) {
            throw new Error('Your Nostr extension does not support NIP-04 encryption');
        }
        return await window.nostr.nip04.encrypt(recipientPubkey, message);
    }

    async decryptMessage(senderPubkey, encryptedMessage) {
        if (!window.nostr.nip04) {
            throw new Error('Your Nostr extension does not support NIP-04 decryption');
        }
        return await window.nostr.nip04.decrypt(senderPubkey, encryptedMessage);
    }

    async signEvent(event) {
        return await window.nostr.signEvent(event);
    }

    connect() {
        CONFIG.RELAYS.forEach(url => this.connectRelay(url));
    }

    connectRelay(url) {
        try {
            const ws = new WebSocket(url);
            
            ws.onopen = () => {
                console.log(`Connected to ${url}`);
                this.relays.set(url, { ws, status: 'connected' });
                this.updateConnectionStatus();
                this.resubscribe(url);
            };

            ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                this.handleMessage(data, url);
            };

            ws.onclose = () => {
                this.relays.set(url, { ws, status: 'disconnected' });
                this.updateConnectionStatus();
                setTimeout(() => this.connectRelay(url), 5000);
            };

            ws.onerror = (error) => {
                console.error(`Relay ${url} error:`, error);
                this.relays.set(url, { ws, status: 'error' });
            };
        } catch (err) {
            console.error(`Failed to connect to ${url}:`, err);
        }
    }

    updateConnectionStatus() {
        const connected = Array.from(this.relays.values()).filter(r => r.status === 'connected').length;
        const total = CONFIG.RELAYS.length;
        
        const statusEl = document.getElementById('relay-status');
        if (statusEl) {
            if (connected === 0) {
                statusEl.innerHTML = '<span class="w-1.5 h-1.5 bg-red-500 rounded-full"></span> Disconnected';
            } else if (connected < total) {
                statusEl.innerHTML = `<span class="w-1.5 h-1.5 bg-yellow-500 rounded-full"></span> ${connected}/${total} relays`;
            } else {
                statusEl.innerHTML = '<span class="w-1.5 h-1.5 bg-green-500 rounded-full"></span> Connected';
            }
        }
    }

    async publishEvent(event) {
        const signedEvent = await this.signEvent(event);
        
        const promises = Array.from(this.relays.entries()).map(async ([url, relay]) => {
            if (relay.status === 'connected' && relay.ws.readyState === WebSocket.OPEN) {
                relay.ws.send(JSON.stringify(['EVENT', signedEvent]));
                return { url, success: true };
            }
            return { url, success: false };
        });

        return Promise.all(promises);
    }

    subscribe(filter, onEvent, relayUrl = null) {
        const subId = Math.random().toString(36).substring(2, 15);
        
        const subscribeToRelay = (url, relay) => {
            if (relay.status === 'connected' && relay.ws.readyState === WebSocket.OPEN) {
                relay.ws.send(JSON.stringify(['REQ', subId, filter]));
            }
        };

        if (relayUrl) {
            const relay = this.relays.get(relayUrl);
            if (relay) subscribeToRelay(relayUrl, relay);
        } else {
            this.relays.forEach(subscribeToRelay);
        }

        this.subscriptions.set(subId, { filter, onEvent });
        return subId;
    }

    resubscribe(relayUrl) {
        const relay = this.relays.get(relayUrl);
        if (!relay || relay.status !== 'connected') return;

        this.subscriptions.forEach((sub, subId) => {
            relay.ws.send(JSON.stringify(['REQ', subId, sub.filter]));
        });
    }

    handleMessage(data, relayUrl) {
        if (data[0] === 'EVENT') {
            const [, subId, event] = data;
            const sub = this.subscriptions.get(subId);
            if (sub) sub.onEvent(event, relayUrl);
        } else if (data[0] === 'EOSE') {
            // End of stored events
        }
    }

    unsubscribe(subId) {
        this.relays.forEach((relay, url) => {
            if (relay.status === 'connected' && relay.ws.readyState === WebSocket.OPEN) {
                relay.ws.send(JSON.stringify(['CLOSE', subId]));
            }
        });
        this.subscriptions.delete(subId);
    }
}

// Chat UI Controller
class ChatUI {
    constructor(nostrClient) {
        this.nostr = nostrClient;
        this.messagesContainer = document.getElementById('chat-container');
        this.messageInput = document.getElementById('message-input');
        this.sendBtn = document.getElementById('send-btn');
        this.isWaiting = false;
        
        this.setupEventListeners();
    }

    setupEventListeners() {
        this.sendBtn.addEventListener('click', () => this.sendMessage());
        this.messageInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.sendMessage();
            }
        });
    }

    async sendMessage() {
        const content = this.messageInput.value.trim();
        if (!content || this.isWaiting) return;

        this.messageInput.value = '';
        this.addMessage(content, 'user');
        this.showTyping();
        this.isWaiting = true;

        try {
            // Encrypt message
            const encrypted = await this.nostr.encryptMessage(CONFIG.CRAB_PUBKEY, content);
            
            // Create NIP-04 DM event
            const event = {
                kind: CONFIG.DM_KIND,
                pubkey: this.nostr.pubkey,
                created_at: Math.floor(Date.now() / 1000),
                tags: [['p', CONFIG.CRAB_PUBKEY]],
                content: encrypted
            };

            await this.nostr.publishEvent(event);
        } catch (err) {
            this.hideTyping();
            this.isWaiting = false;
            showError('Failed to send: ' + err.message);
            console.error(err);
        }
    }

    addMessage(content, sender, timestamp = null) {
        const div = document.createElement('div');
        div.className = `message-bubble flex ${sender === 'user' ? 'justify-end' : 'justify-start'}`;
        
        const isUser = sender === 'user';
        const displayContent = this.escapeHtml(content);
        
        div.innerHTML = `
            <div class="max-w-[80%] ${isUser ? 'bg-crab-600' : 'bg-slate-800'} rounded-2xl px-4 py-3 ${isUser ? 'rounded-br-md' : 'rounded-bl-md'}">
                <p class="text-sm text-slate-100 leading-relaxed">${displayContent}</p>
                <span class="text-[10px] text-slate-400 mt-1 block text-right">${this.formatTime(timestamp)}</span>
            </div>
        `;
        
        this.messagesContainer.appendChild(div);
        this.scrollToBottom();
    }

    showTyping() {
        const div = document.createElement('div');
        div.id = 'typing-indicator';
        div.className = 'message-bubble flex justify-start';
        div.innerHTML = `
            <div class="bg-slate-800 rounded-2xl rounded-bl-md px-4 py-3">
                <div class="flex gap-1">
                    <div class="w-2 h-2 bg-slate-500 rounded-full typing-dot"></div>
                    <div class="w-2 h-2 bg-slate-500 rounded-full typing-dot"></div>
                    <div class="w-2 h-2 bg-slate-500 rounded-full typing-dot"></div>
                </div>
            </div>
        `;
        this.messagesContainer.appendChild(div);
        this.scrollToBottom();
    }

    hideTyping() {
        const indicator = document.getElementById('typing-indicator');
        if (indicator) indicator.remove();
    }

    scrollToBottom() {
        this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    formatTime(timestamp) {
        if (!timestamp) {
            const now = new Date();
            return now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        }
        const date = new Date(timestamp * 1000);
        return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }
}

// Main Application
const nostr = new NostrClient();
let chatUI = null;
let messageSub = null;

function showError(msg) {
    const toast = document.getElementById('error-toast');
    const message = document.getElementById('error-message');
    message.textContent = msg;
    toast.classList.remove('translate-x-full');
    setTimeout(() => toast.classList.add('translate-x-full'), 5000);
}

async function initLogin() {
    const loginBtn = document.getElementById('login-btn');
    
    loginBtn.addEventListener('click', async () => {
        try {
            loginBtn.disabled = true;
            loginBtn.innerHTML = '<div class="w-5 h-5 border-2 border-white/30 border-t-white rounded-full animate-spin"></div> Connecting...';
            
            const pubkey = await nostr.getPublicKey();
            localStorage.setItem('hermitcrab_pubkey', CONFIG.CRAB_PUBKEY);
            
            // Show chat screen
            document.getElementById('login-screen').classList.add('hidden');
            document.getElementById('chat-screen').classList.remove('hidden');
            document.getElementById('user-npub').textContent = pubkey.substring(0, 16) + '...';
            
            // Init chat
            chatUI = new ChatUI(nostr);
            
            // Connect to relays and subscribe to DMs
            nostr.connect();
            startMessageListener(pubkey);
            
        } catch (err) {
            showError(err.message);
            loginBtn.disabled = false;
            loginBtn.innerHTML = `
                <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z"></path>
                </svg>
                Connect with Nostr
            `;
        }
    });
}

function startMessageListener(userPubkey) {
    // Subscribe to DMs from HermitCrab
    messageSub = nostr.subscribe(
        {
            kinds: [CONFIG.DM_KIND],
            '#p': [userPubkey],
            since: Math.floor(Date.now() / 1000)
        },
        async (event) => {
            // Only process messages from HermitCrab
            if (event.pubkey !== CONFIG.CRAB_PUBKEY) return;
            
            try {
                const decrypted = await nostr.decryptMessage(event.pubkey, event.content);
                chatUI.hideTyping();
                chatUI.addMessage(decrypted, 'crab', event.created_at);
                chatUI.isWaiting = false;
            } catch (err) {
                console.error('Failed to decrypt message:', err);
            }
        }
    );
}

// Logout
function initLogout() {
    const logoutBtn = document.getElementById('logout-btn');
    logoutBtn.addEventListener('click', () => {
        // Clear subscription
        if (messageSub) nostr.unsubscribe(messageSub);
        
        // Disconnect relays
        nostr.relays.forEach(relay => {
            if (relay.ws) relay.ws.close();
        });
        
        // Reset UI
        chatUI = null;
        messageSub = null;
        nostr.pubkey = null;
        
        document.getElementById('chat-screen').classList.add('hidden');
        document.getElementById('login-screen').classList.remove('hidden');
        document.getElementById('chat-container').innerHTML = `
            <div class="flex justify-center">
                <span class="text-xs text-slate-600 bg-slate-900/50 px-3 py-1 rounded-full">Today</span>
            </div>
        `;
    });
}

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    initLogin();
    initLogout();
});
