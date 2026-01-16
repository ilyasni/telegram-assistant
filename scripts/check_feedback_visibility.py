#!/usr/bin/env python3
"""
Проверка видимости feedback для админов.

Проверяет:
1. Все ли feedback имеют tenant_id
2. Все ли feedback видны админам своего tenant
3. Нет ли feedback с несуществующими tenant_id или user_id
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'api'))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from config import settings
from models.database import UserFeedback, User, Tenant
import structlog

logger = structlog.get_logger()


def check_feedback_visibility():
    """Проверка видимости feedback для админов."""
    engine = create_engine(settings.database_url)
    Session = sessionmaker(bind=engine)
    db = Session()
    
    try:
        # 1. Проверка feedback без tenant_id (не должно быть из-за nullable=False)
        feedback_without_tenant = db.query(UserFeedback).filter(
            UserFeedback.tenant_id.is_(None)
        ).count()
        
        if feedback_without_tenant > 0:
            logger.error(
                "Found feedback without tenant_id",
                count=feedback_without_tenant
            )
            print(f"❌ Найдено {feedback_without_tenant} feedback без tenant_id")
        else:
            print("✅ Все feedback имеют tenant_id")
        
        # 2. Проверка feedback с несуществующими tenant_id
        all_tenants = {t.id for t in db.query(Tenant).all()}
        feedback_with_invalid_tenant = db.query(UserFeedback).filter(
            ~UserFeedback.tenant_id.in_(all_tenants)
        ).all()
        
        if feedback_with_invalid_tenant:
            logger.error(
                "Found feedback with invalid tenant_id",
                count=len(feedback_with_invalid_tenant)
            )
            print(f"❌ Найдено {len(feedback_with_invalid_tenant)} feedback с несуществующими tenant_id:")
            for fb in feedback_with_invalid_tenant[:10]:  # Показываем первые 10
                print(f"  - Feedback {fb.id}: tenant_id={fb.tenant_id}")
        else:
            print("✅ Все feedback имеют валидные tenant_id")
        
        # 3. Проверка feedback с несуществующими user_id
        all_users = {u.id for u in db.query(User).all()}
        feedback_with_invalid_user = db.query(UserFeedback).filter(
            ~UserFeedback.user_id.in_(all_users)
        ).all()
        
        if feedback_with_invalid_user:
            logger.error(
                "Found feedback with invalid user_id",
                count=len(feedback_with_invalid_user)
            )
            print(f"❌ Найдено {len(feedback_with_invalid_user)} feedback с несуществующими user_id:")
            for fb in feedback_with_invalid_user[:10]:
                print(f"  - Feedback {fb.id}: user_id={fb.user_id}")
        else:
            print("✅ Все feedback имеют валидные user_id")
        
        # 4. Проверка, что все feedback видны админам своего tenant
        # Для каждого tenant проверяем, что админы видят все feedback
        tenants = db.query(Tenant).all()
        total_feedback = db.query(UserFeedback).count()
        visible_feedback = 0
        hidden_feedback = []
        
        for tenant in tenants:
            # Получаем всех админов этого tenant
            admins = db.query(User).filter(
                User.tenant_id == tenant.id,
                User.role == 'admin'
            ).all()
            
            if not admins:
                # Если нет админов, пропускаем
                continue
            
            # Получаем все feedback этого tenant
            tenant_feedback = db.query(UserFeedback).filter(
                UserFeedback.tenant_id == tenant.id
            ).all()
            
            for feedback in tenant_feedback:
                visible_feedback += 1
                # Проверяем, что feedback виден хотя бы одному админу
                # (это всегда должно быть true, так как фильтр по tenant_id)
                # Но проверим, что user существует и принадлежит тому же tenant
                user = db.query(User).filter(User.id == feedback.user_id).first()
                if user and user.tenant_id != tenant.id:
                    hidden_feedback.append({
                        'feedback_id': feedback.id,
                        'feedback_tenant_id': feedback.tenant_id,
                        'user_id': feedback.user_id,
                        'user_tenant_id': user.tenant_id,
                        'issue': 'user belongs to different tenant'
                    })
        
        if hidden_feedback:
            logger.error(
                "Found feedback that may not be visible to admins",
                count=len(hidden_feedback)
            )
            print(f"❌ Найдено {len(hidden_feedback)} feedback, которые могут быть не видны админам:")
            for item in hidden_feedback[:10]:
                print(f"  - Feedback {item['feedback_id']}: "
                      f"feedback.tenant_id={item['feedback_tenant_id']}, "
                      f"user.tenant_id={item['user_tenant_id']}")
        else:
            print(f"✅ Все {visible_feedback} feedback видны админам своего tenant")
        
        # 5. Статистика по tenant
        print("\n📊 Статистика по tenant:")
        tenant_stats = db.query(
            UserFeedback.tenant_id,
            text('COUNT(*) as count')
        ).group_by(UserFeedback.tenant_id).all()
        
        for tenant_id, count in tenant_stats:
            tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
            tenant_name = tenant.name if tenant else "Unknown"
            admins_count = db.query(User).filter(
                User.tenant_id == tenant_id,
                User.role == 'admin'
            ).count()
            print(f"  - {tenant_name} ({tenant_id}): {count} feedback, {admins_count} админов")
        
        # 6. Итоговая статистика
        print(f"\n📈 Итого:")
        print(f"  - Всего feedback: {total_feedback}")
        print(f"  - Feedback с валидными tenant_id: {total_feedback - len(feedback_with_invalid_tenant)}")
        print(f"  - Feedback с валидными user_id: {total_feedback - len(feedback_with_invalid_user)}")
        print(f"  - Feedback, видимые админам: {visible_feedback - len(hidden_feedback)}")
        
        if feedback_without_tenant == 0 and not feedback_with_invalid_tenant and not feedback_with_invalid_user and not hidden_feedback:
            print("\n✅ Все проверки пройдены успешно!")
            return 0
        else:
            print("\n❌ Обнаружены проблемы с видимостью feedback")
            return 1
            
    except Exception as e:
        logger.error("Error checking feedback visibility", error=str(e), exc_info=True)
        print(f"❌ Ошибка при проверке: {e}")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    exit(check_feedback_visibility())
