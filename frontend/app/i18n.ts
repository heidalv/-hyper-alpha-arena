/**
 * i18n configuration for Heidalv Alpha Arena
 * - Default language: Chinese
 * - Persist user's manual selection to localStorage
 * - Fallback to Chinese for missing translations
 */

import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import LanguageDetector from 'i18next-browser-languagedetector'

import en from './locales/en.json'
import zh from './locales/zh.json'

const resources = {
  en: { translation: en },
  zh: { translation: zh }
}

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources,
    fallbackLng: 'zh',
    supportedLngs: ['zh', 'en'],
    lng: 'zh', // 默认语言为中文

    // Language detection options
    detection: {
      // Detection order: localStorage first, then browser language
      order: ['localStorage', 'navigator'],
      // Cache user's selection in localStorage
      caches: ['localStorage'],
      // localStorage key name
      lookupLocalStorage: 'arena-language'
    },

    interpolation: {
      escapeValue: false // React already escapes
    },

    // Return key as fallback (will show English from fallbackLng)
    returnEmptyString: false,

    // Don't load missing keys from backend
    saveMissing: false
  })

export default i18n
