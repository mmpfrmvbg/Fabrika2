/**
 * API-клиент для выполнения HTTP-запросов.
 * Предоставляет единую функцию fetch с обработкой таймаутов и ошибок.
 * @module api
 * @version 1.0.1
 */

export interface ApiResponse<T = unknown> {
  data: T | null;
  error: string | null;
  status: number;
}

export interface FetchOptions extends RequestInit {
  timeout?: number;
}

const DEFAULT_TIMEOUT = 30000;

/**
 * Выполняет HTTP-запрос с поддержкой таймаута и обработкой ошибок.
 * @param url - URL для запроса
 * @param options - Опции запроса (включая timeout)
 * @returns Promise с результатом запроса
 */
export async function fetch<T = unknown>(
  url: string,
  options: FetchOptions = {}
): Promise<ApiResponse<T>> {
  const { timeout = DEFAULT_TIMEOUT, ...fetchOptions } = options;

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeout);

  try {
    const response = await globalThis.fetch(url, {
      ...fetchOptions,
      signal: controller.signal,
    });

    clearTimeout(timeoutId);

    if (!response.ok) {
      return {
        data: null,
        error: `HTTP error: ${response.status} ${response.statusText}`,
        status: response.status,
      };
    }

    const data = await response.json();
    return {
      data: data as T,
      error: null,
      status: response.status,
    };
  } catch (error) {
    clearTimeout(timeoutId);

    if (error instanceof Error && error.name === 'AbortError') {
      return {
        data: null,
        error: `Request timeout after ${timeout}ms`,
        status: 408,
      };
    }

    return {
      data: null,
      error: error instanceof Error ? error.message : 'Unknown error',
      status: 0,
    };
  }
}
