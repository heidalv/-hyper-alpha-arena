import { apiRequest } from './client'

export const getCryptoPrice = (symbol: string) =>
  apiRequest<any>(`/crypto/price/${symbol}`)

export const getCryptoSymbols = () =>
  apiRequest<any>('/crypto/symbols')

export const getKline = (symbol: string, period = '1h', count = 200) =>
  apiRequest<any[]>(`/kline/${symbol}?period=${period}&count=${count}`)
