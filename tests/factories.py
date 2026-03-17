# tests/factories.py
import factory
from django.contrib.auth.models import User
from faker import Faker

fake = Faker()


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User

    username = factory.LazyFunction(fake.user_name)
    is_staff = True
    is_active = True


class LeadFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "crm.Lead"

    first_name = factory.LazyFunction(fake.first_name)
    last_name = factory.LazyFunction(fake.last_name)
    linkedin_url = factory.LazyFunction(
        lambda: f"https://www.linkedin.com/in/{fake.user_name()}/"
    )


class DealFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "crm.Deal"

    lead = factory.SubFactory(LeadFactory)
