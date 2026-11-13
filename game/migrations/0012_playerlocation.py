import uuid
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [('game', '0011_game_zone_bonus_processed_at')]
    operations = [migrations.CreateModel(
        name='PlayerLocation',
        fields=[
            ('id', models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
            ('latitude', models.FloatField()),
            ('longitude', models.FloatField()),
            ('updated_at', models.DateTimeField(default=django.utils.timezone.now, db_index=True)),
            ('team', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='game.team')),
        ],
    )]
