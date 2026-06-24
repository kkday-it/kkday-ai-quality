// Products 領域 API。
import { BASE, j } from './http.api';

export const getProducts = () => j(`${BASE}/products`);
