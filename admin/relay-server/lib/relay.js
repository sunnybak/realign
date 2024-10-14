import { WebSocketServer, WebSocket } from 'ws';
import { RealtimeClient } from '@openai/realtime-api-beta';
import express from 'express';
import http from 'http';
import OpenAI from 'openai';
import cors from 'cors'; // Add this import

const client = new OpenAI({ apiKey: process.env.OPENAI_API_KEY });

const HTTP_PORT = 9000;
const WS_PORT = 8081;

export class RealtimeRelay {
  constructor(apiKey) {
    this.apiKey = apiKey;
    this.sockets = new WeakMap();
    this.wss = null;
    this.conversation = [];
    this.instructions = "";
    this.app = express();
    this.httpServer = null;
    this.pushFeedbackInterval = null;
    this.bestIdea = null;
    this.openai = new OpenAI({ apiKey: this.apiKey }); // Initialize OpenAI client

    this.table = []
    this.processedFeedback = ""
    this.processed_ideas = []
  }

  listen(port) {
    this.setupExpressRoutes();
    this.httpServer = http.createServer(this.app);
    this.app.listen(HTTP_PORT, () => {
      this.log(`HTTP server listening on http://localhost:${HTTP_PORT}`);
    });

    this.wss = new WebSocketServer({ port: port });

    this.wss.on('connection', this.connectionHandler.bind(this));
    this.log(`WebSocket server listening on ws://localhost:${port}`);
    
    // Start the ping loop when the server starts listening
    this.startPingLoop();
  }

  setupExpressRoutes() {
    this.app.use(cors()); // Add CORS middleware
    this.app.use(express.json()); // Middleware to parse JSON bodies

    // make a get table endpoint that returns the table
    this.app.get('/table', (req, res) => {
      console.log('Received GET request to /table');
      console.log('Table:', this.table);
      res.json(this.table);
    });

    this.app.get('/processed_feedback', (req, res) => {
      console.log('Received GET request to /processed_feedback');
      console.log('Processed feedback:', this.processedFeedback);
      res.json(this.processedFeedback);
    });

    this.app.get('/processed_ideas', (req, res) => {
      console.log('Received GET request to /processed_ideas');
      console.log('Processed ideas:', this.processed_ideas);
      res.json({ processed_ideas: this.processed_ideas });
    });

    this.app.post('/processed_ideas', (req, res) => {
      console.log('Received POST request to /processed_ideas');
      console.log('Request body:', req.body);
      this.processed_ideas = req.body.processed_ideas;
      res.status(200).json({ message: 'Processed ideas received' });
    });

    this.app.post('/idea', (req, res) => {
      console.log('Received POST request to /idea');
      console.log('Request body:', req.body);
      const ideas = req.body.ideas;
      if (Array.isArray(ideas)) {
        ideas.forEach(idea => {
          this.table.push(idea);
        });
      } else {
        this.table.push(req.body);
      }
      res.status(200).json({ message: 'Idea(s) received' });
    });

    // raw_feedback
    this.app.post('/raw_feedback', (req, res) => {
      console.log('Received POST request to /raw_feedback');
      console.log('Request body:', req.body);
      res.status(200).json({ message: 'Feedback received' });
    });
  }

  async connectionHandler(ws, req) {
    if (!req.url) {
      this.log('No URL provided, closing connection.');
      ws.close();
      return;
    }

    const url = new URL(req.url, `http://${req.headers.host}`);
    const pathname = url.pathname;

    if (pathname !== '/') {
      this.log(`Invalid pathname: "${pathname}"`);
      ws.close();
      return;
    }

    // Instantiate new client
    this.log(`Connecting with key "${this.apiKey.slice(0, 3)}..."`);
    const client = new RealtimeClient({ apiKey: this.apiKey });

    // Relay: OpenAI Realtime API Event -> Browser Event and Relay Server
    client.realtime.on('server.*', (event) => {
      // this.log(`Relaying "${event.type}" to Client and Relay Server`);
      ws.send(JSON.stringify(event));

      this.conversation = client.conversation.getItems().map(item => ({
        role: item.role,
        content: item.formatted.transcript || item.formatted.text
      })).filter(item => item.content);
      this.conversation.unshift({
        role: 'system',
        content: this.instructions.toString()
      });
      if (event.type === 'session.updated' && event.session.instructions) {
        this.instructions = event.session.instructions;
      }
      // console.log(this.conversation);
    });
    client.realtime.on('close', () => ws.close());

    // Relay: Browser Event -> OpenAI Realtime API Event and Relay Server
    const messageQueue = [];
    const messageHandler = (data) => {
      try {
        const event = JSON.parse(data);
        // this.log(`Relaying "${event.type}" to OpenAI and Relay Server`);
        client.realtime.send(event.type, event);
      } catch (e) {
        console.error(e.message);
        this.log(`Error parsing event from client: ${data}`);
      }
    };
    ws.on('message', (data) => {
      if (!client.isConnected()) {
        messageQueue.push(data);
      } else {
        messageHandler(data);
      }
    });
    ws.on('close', () => client.disconnect());

    // Connect to OpenAI Realtime API
    try {
      this.log(`Connecting to OpenAI...`);
      await client.connect();
    } catch (e) {
      this.log(`Error connecting to OpenAI: ${e.message}`);
      ws.close();
      return;
    }
    this.log(`Connected to OpenAI successfully!`);
    while (messageQueue.length) {
      messageHandler(messageQueue.shift());
    }
  }

  log(...args) {
    console.log(`[RealtimeRelay]`, ...args);
  }

  async processFeedback(feedback) {
    try {
      const response = await client.chat.completions.create({
        model: "gpt-4o-mini",
        messages: [
          { role: "system", content: "You are a helpful assistant first prints the conversation summary in bullet points. Then, it gives any search criteria relevant to the conversation. If the conversation is empty or hasn't started, just return saying to continue a general search for good business ideas." },
          { role: "user", content: `Here is the conversation: ${JSON.stringify(feedback)}` }
        ],
        max_tokens: 150
      });

      // console.log('OpenAI response', response);

      return response.choices[0].message.content;
    } catch (error) {
      console.error('Error processing feedback with OpenAI:', error);
      return JSON.stringify(feedback); // Return original feedback if processing fails
    }
  }

  startPingLoop() {
    const sendFeedback = async (feedback) => {
      // console.log('Original feedback', feedback);
      
      // Process feedback using OpenAI
      this.processedFeedback = await this.processFeedback(feedback);

      try {
        const response = await fetch(`http://127.0.0.1:7000/feedback`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ feedback: this.processedFeedback }),
        });
        
        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        const text = await response.text();
        let data;
        try {
          data = JSON.parse(text);
        } catch (e) {
          console.error('Error parsing JSON:', e);
          console.log('Raw response:', text);
          throw new Error('Invalid JSON response');
        }
        
        console.log('Feedback sent successfully', data);
        return { success: true, message: 'Feedback sent successfully', data };
      } catch (error) {
        console.error('Error sending feedback:', error);
        return { success: false, message: 'Failed to send feedback' };
      }
    };

    const makeApiCall = async () => {

      // for feedback, send the stringified this.conversation
      await sendFeedback(JSON.stringify(this.conversation));
      makeApiCall(); // Call the function again after the previous call completes
    };

    makeApiCall(); // Start the continuous API call loop
  }

  stopPingLoop() {
    if (this.pingInterval) {
      clearInterval(this.pingInterval);
      this.pingInterval = null;
      this.log('Stopped ping loop');
    }
  }
}
