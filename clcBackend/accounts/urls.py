from django.urls import path

from . import views

urlpatterns = [
    path('login/', views.login),
    path('select-role/', views.select_role),
    path('change-password/', views.change_password),
    path('refresh/', views.refresh),
    path('logout/', views.logout),
]
