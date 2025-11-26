/**
 * Azure AI Search Knowledge Agent Client
 *
 * Direct API client for Azure AI Search Knowledge Agent API.
 * This eliminates the need for a backend server by calling the Agent API directly.
 *
 * @see https://learn.microsoft.com/en-us/azure/search/retrieval-augmented-generation
 */

export interface Message {
  role: 'system' | 'user' | 'assistant';
  content: string;
}

export interface AgentReference {
  title: string;
  url?: string;
  chunk?: string;
}

export interface AgentResponse {
  answer: string;
  references: AgentReference[];
  model?: string;
  activity?: any; // Agent activity log
}

export interface AgentError {
  error: {
    code: string;
    message: string;
  };
}

class AzureAgentClient {
  private searchEndpoint: string;
  private agentName: string;
  private apiVersion: string;
  private apiKey: string;

  constructor() {
    // Configuration from environment variables
    this.searchEndpoint = process.env.NEXT_PUBLIC_SEARCH_ENDPOINT || '';
    this.agentName = process.env.NEXT_PUBLIC_AGENT_NAME || 'rush-policy-agent';
    this.apiVersion = process.env.NEXT_PUBLIC_API_VERSION || '2025-05-01-preview';
    this.apiKey = process.env.NEXT_PUBLIC_SEARCH_API_KEY || '';

    if (!this.searchEndpoint) {
      console.error('NEXT_PUBLIC_SEARCH_ENDPOINT is not configured');
    }
    if (!this.apiKey) {
      console.error('NEXT_PUBLIC_SEARCH_API_KEY is not configured');
    }
  }

  /**
   * Query the Knowledge Agent with a user message
   *
   * @param userMessage - The user's question
   * @param conversationHistory - Previous messages in the conversation (optional)
   * @returns Agent response with answer and references
   */
  async query(
    userMessage: string,
    conversationHistory: Message[] = []
  ): Promise<AgentResponse> {
    if (!this.searchEndpoint || !this.apiKey) {
      throw new Error('Azure Search is not properly configured. Check environment variables.');
    }

    if (!userMessage.trim()) {
      throw new Error('User message cannot be empty');
    }

    // Construct the API URL
    const url = `${this.searchEndpoint}/agents/${this.agentName}/retrieve?api-version=${this.apiVersion}`;

    // Prepare messages payload
    const messages: Message[] = [
      ...conversationHistory,
      { role: 'user', content: userMessage }
    ];

    try {
      const response = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'api-key': this.apiKey,
        },
        body: JSON.stringify({ messages }),
      });

      if (!response.ok) {
        const errorData: AgentError = await response.json().catch(() => ({
          error: {
            code: 'UNKNOWN_ERROR',
            message: `HTTP ${response.status}: ${response.statusText}`
          }
        }));

        throw new Error(
          errorData.error?.message || `Agent API request failed with status ${response.status}`
        );
      }

      const data = await response.json();

      // Parse the agent response
      // The exact structure may vary - adjust based on actual API response
      return this.parseAgentResponse(data);

    } catch (error) {
      console.error('Knowledge Agent API error:', error);

      if (error instanceof Error) {
        throw error;
      }

      throw new Error('Failed to query Knowledge Agent');
    }
  }

  /**
   * Parse the raw agent API response into a standardized format
   */
  private parseAgentResponse(data: any): AgentResponse {
    // The response structure depends on the Agent API version
    // Common structures:
    // 1. { answer: "...", references: [...] }
    // 2. { messages: [...], references: [...] }
    // 3. { result: { answer: "...", citations: [...] } }

    let answer = '';
    let references: AgentReference[] = [];
    let model: string | undefined;
    let activity: any;

    // Extract answer from various possible structures
    if (data.answer) {
      answer = data.answer;
    } else if (data.messages && data.messages.length > 0) {
      const lastMessage = data.messages[data.messages.length - 1];
      answer = lastMessage.content || '';
    } else if (data.result?.answer) {
      answer = data.result.answer;
    } else if (data.choices && data.choices.length > 0) {
      // OpenAI-style response
      answer = data.choices[0].message?.content || '';
    }

    // Extract references/citations
    if (data.references && Array.isArray(data.references)) {
      references = data.references.map((ref: any) => ({
        title: ref.title || ref.name || 'Unknown',
        url: ref.url || ref.uri,
        chunk: ref.chunk || ref.content || ref.text,
      }));
    } else if (data.citations && Array.isArray(data.citations)) {
      references = data.citations.map((citation: any) => ({
        title: citation.title || citation.filepath || 'Unknown',
        url: citation.url || citation.uri,
        chunk: citation.content || citation.chunk,
      }));
    } else if (data.context?.data_points) {
      // Azure AI Search RAG pattern
      references = data.context.data_points.map((dp: any) => ({
        title: dp.title || 'Policy Document',
        url: dp.url,
        chunk: dp.text || dp.chunk,
      }));
    }

    model = data.model || data.deployment_id;
    activity = data.activity;

    return {
      answer: answer.trim(),
      references,
      model,
      activity,
    };
  }

  /**
   * Health check for the Knowledge Agent
   * @returns true if agent is accessible
   */
  async healthCheck(): Promise<boolean> {
    if (!this.searchEndpoint || !this.apiKey) {
      return false;
    }

    try {
      // Try a simple query
      const response = await this.query('test', []);
      return !!response.answer;
    } catch (error) {
      console.error('Knowledge Agent health check failed:', error);
      return false;
    }
  }

  /**
   * Get agent configuration
   */
  getConfig() {
    return {
      endpoint: this.searchEndpoint,
      agentName: this.agentName,
      apiVersion: this.apiVersion,
      isConfigured: !!(this.searchEndpoint && this.apiKey),
    };
  }
}

// Singleton instance
export const azureAgentClient = new AzureAgentClient();

// Export for use in components
export default azureAgentClient;
