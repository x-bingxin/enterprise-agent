import CustomerChat from '../views/CustomerChat.vue'
import AdminDashboard from '../views/AdminDashboard.vue'
import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'customer', component: CustomerChat },
    { path: '/admin', name: 'admin', component: AdminDashboard },
  ]
})

export default router