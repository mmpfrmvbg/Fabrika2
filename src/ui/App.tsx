import React, { useEffect, useState } from 'react';

/**
 * @fileoverview Компонент App отображает данные из API с обработкой загрузки и ошибок.
 */

/**
 * Элемент данных получаемый из API.
 */
interface DataItem {
  id: number;
  name: string;
}

/**
 * Ответ от API с данными.
 */
interface ApiResponse {
  data: DataItem[];
}

/**
 * Основной компонент приложения.
 * Отображает данные из API с обработкой состояний загрузки и ошибок.
 * @returns JSX элемент с списком данных или сообщением о загрузке/ошибке
 */
export const App: React.FC = () => {
  const [data, setData] = useState<DataItem[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchData = async () => {
      try {
        setLoading(true);
        setError(null);
        const response = await fetch('/api/data');
        if (!response.ok) {
          throw new Error(`HTTP error: ${response.status}`);
        }
        const result: ApiResponse = await response.json();
        setData(result.data);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unknown error');
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, []);

  if (loading) {
    return <div data-testid="loading">Loading...</div>;
  }

  if (error) {
    return <div data-testid="error">Error: {error}</div>;
  }

  return (
    <div data-testid="data">
      <h1>Data List</h1>
      <ul>
        {data.map((item) => (
          <li key={item.id}>{item.name}</li>
        ))}
      </ul>
    </div>
  );
};

export default App;
