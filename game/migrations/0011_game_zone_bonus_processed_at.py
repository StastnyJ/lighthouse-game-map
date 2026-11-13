from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('game', '0010_gamesnapshot')]

    operations = [
        migrations.AddField(
            model_name='game',
            name='zone_bonus_processed_at',
            field=models.DateTimeField(null=True, blank=True),
        ),
    ]
