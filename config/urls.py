from django.contrib import admin
from django.contrib.auth import views as auth
from django.urls import path
from crm import views
urlpatterns = [path('admin/',admin.site.urls),path('login/',auth.LoginView.as_view()),path('logout/',auth.LogoutView.as_view()),path('',views.companies),path('companies/<int:pk>/',views.company),path('imports/',views.imports),path('imports/reset/',views.reset_imports),path('imports/progress/',views.import_progress),path('jobs/<int:pk>/',views.job_status),path('imports/<int:pk>/export/',views.day_export),path('statistics/',views.statistics),path('companies/export/',views.companies_export)]
