import React from 'react';
import { strings } from '../i18n/strings';
import {
  ActionButton,
  AppText,
  Heading,
  Screen,
} from '../components/ui/Primitives';

export function BootstrapFailureScreen({ onRetry }: { onRetry: () => void }) {
  return (
    <Screen testID="bootstrap-failure-screen">
      <Heading>{strings.bootstrap.retryTitle}</Heading>
      <AppText style={{ marginTop: 12 }}>{strings.bootstrap.retryBody}</AppText>
      <ActionButton
        label={strings.bootstrap.retryAction}
        onPress={onRetry}
        style={{ marginTop: 24 }}
      />
    </Screen>
  );
}
